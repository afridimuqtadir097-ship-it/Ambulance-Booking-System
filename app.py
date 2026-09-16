import logging
import math
import os
from datetime import datetime
from functools import wraps

from flask import Flask, render_template, redirect, url_for, flash, request, jsonify, abort, session
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy.exc import IntegrityError

from config import Config
from models import db, User, Ambulance, Booking, Notification, EMERGENCY_TYPES, VEHICLE_TYPES

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

csrf = CSRFProtect(app)

# Rate-limit brute-force-able endpoints (login/registration) by client IP.
# In-memory storage is fine for a single-process deployment (e.g. Render's
# free tier); see config.py's RATELIMIT_STORAGE_URI if you ever run more
# than one worker process/instance and need limits shared across them.
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    storage_uri=app.config.get("RATELIMIT_STORAGE_URI", "memory://"),
    default_limits=[],
)

# ---------------------------------------------------------------------------
# Logging -- prints are fine for a quick script, but a real deployment needs
# timestamped, leveled log output that a host's log viewer (e.g. Render's
# logs tab) can filter on. Flask's own app.logger is a standard
# `logging.Logger`; we just make sure it actually emits somewhere with a
# useful format, since Flask only auto-attaches a handler when running its
# own dev server -- not under gunicorn.
if not app.logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)s in %(module)s: %(message)s"
    ))
    app.logger.addHandler(_handler)
app.logger.setLevel(logging.DEBUG if app.config["DEBUG"] else logging.INFO)

# Make these available in every template without passing them explicitly.
app.jinja_env.globals["EMERGENCY_TYPES"] = EMERGENCY_TYPES
app.jinja_env.globals["VEHICLE_TYPES"] = VEHICLE_TYPES


# ---------------------------------------------------------------------------
# Three fully independent sessions
# ---------------------------------------------------------------------------
# Each portal (patient / driver / admin) keeps its own identity under its own
# key in the session, instead of one shared login session. Logging into one
# portal never touches or overwrites the others -- a person can be logged
# into all three simultaneously in the same browser without them colliding,
# and visiting one portal's login page while logged into a *different*
# portal will not bounce them back to that other portal.

class AnonymousIdentity:
    is_authenticated = False
    role = None
    name = ""
    email = ""


def get_current_patient():
    uid = session.get("patient_id")
    return User.query.filter_by(id=uid, role="patient").first() if uid else None


def get_current_driver():
    uid = session.get("driver_id")
    return User.query.filter_by(id=uid, role="driver").first() if uid else None


def get_current_admin():
    uid = session.get("admin_id")
    return User.query.filter_by(id=uid, role="admin").first() if uid else None


def patient_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not get_current_patient():
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapped


def driver_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not get_current_driver():
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("driver_login"))
        return f(*args, **kwargs)
    return wrapped


def admin_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not get_current_admin():
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return wrapped


@app.context_processor
def inject_pagination_helper():
    def url_for_page(page):
        """Build a link to the current view with only the `page` query
        param changed, preserving everything else (e.g. ?status=... on the
        admin bookings filter)."""
        args = dict(request.view_args or {})
        args.update(request.args.to_dict())
        args["page"] = page
        return url_for(request.endpoint, **args)
    return {"url_for_page": url_for_page}


@app.context_processor
def inject_portal_identity():
    """Templates keep referencing `current_user` like before -- it now
    resolves to whichever portal's *own* session key matches the section of
    the site currently being viewed, rather than one shared login."""
    if request.path.startswith("/driver"):
        user = get_current_driver()
    elif request.path.startswith("/admin"):
        user = get_current_admin()
    else:
        user = get_current_patient()
    return {"current_user": user or AnonymousIdentity()}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def haversine_km(lat1, lng1, lat2, lng2):
    """Great-circle distance in kilometers between two lat/lng points."""
    if None in (lat1, lng1, lat2, lng2):
        return float("inf")
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def find_nearest_available_ambulance(lat, lng, exclude_ids=None):
    exclude_ids = exclude_ids or []
    candidates = Ambulance.query.filter_by(status="available").filter(
        Ambulance.lat.isnot(None), Ambulance.lng.isnot(None)
    ).all()
    candidates = [a for a in candidates if a.id not in exclude_ids]
    if not candidates:
        return None
    candidates.sort(key=lambda a: haversine_km(lat, lng, a.lat, a.lng))
    return candidates[0]


def notify(user_id, message):
    db.session.add(Notification(user_id=user_id, message=message))


def try_serve_waiting_booking(ambulance):
    """Call this whenever an ambulance transitions to 'available' (a driver
    goes online, a trip completes, a booking is cancelled/rejected). If any
    booking is stuck in 'no_ambulance' status -- because nothing was free
    when it was originally requested -- give the oldest one first claim on
    this newly-freed vehicle, instead of leaving it to sit unresolved until
    the patient notices and re-books manually."""
    if ambulance.status != "available":
        return
    waiting = (
        Booking.query.filter_by(status="no_ambulance")
        .order_by(Booking.created_at.asc())
        .all()
    )
    for booking in waiting:
        if ambulance.id in booking.rejected_list():
            continue
        ambulance.status = "busy"
        booking.ambulance_id = ambulance.id
        booking.status = "requested"
        if ambulance.driver_id:
            notify(ambulance.driver_id, f"New emergency request #{booking.id} — please respond.")
        db.session.commit()
        return


def dispatch_booking(booking):
    """Assign the nearest available ambulance to a pending booking."""
    ambulance = find_nearest_available_ambulance(
        booking.pickup_lat, booking.pickup_lng, exclude_ids=booking.rejected_list()
    )
    if ambulance:
        ambulance.status = "busy"
        booking.ambulance_id = ambulance.id
        booking.status = "requested"
        if ambulance.driver_id:
            notify(ambulance.driver_id, f"New emergency request #{booking.id} — please respond.")
    else:
        booking.status = "no_ambulance"
    db.session.commit()
    return ambulance


def validate_registration(name, email, phone, password, confirm):
    """Shared validation for the patient and driver sign-up forms. Returns an
    error message string, or None if the submission is valid."""
    if not all([name, email, phone, password]):
        return "Please fill in all fields."
    if password != confirm:
        return "Passwords do not match."
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if User.query.filter_by(email=email).first():
        return "An account with this email already exists."
    return None


def handle_profile_update(user):
    """Shared handler for the details/password forms on the profile page,
    used by all three portals' profile routes."""
    form_type = request.form.get("form_type")

    if form_type == "details":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        if not name or not phone:
            flash("Name and phone are required.", "danger")
        else:
            user.name = name
            user.phone = phone
            db.session.commit()
            flash("Profile updated.", "success")

    elif form_type == "password":
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")
        if not user.check_password(current_pw):
            flash("Current password is incorrect.", "danger")
        elif len(new_pw) < 8:
            flash("New password must be at least 8 characters.", "danger")
        elif new_pw != confirm_pw:
            flash("New passwords do not match.", "danger")
        else:
            user.set_password(new_pw)
            db.session.commit()
            flash("Password changed successfully.", "success")


# ---------------------------------------------------------------------------
# Patient portal (public-facing) -- the only portal linked from the homepage.
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    if get_current_patient():
        return redirect(url_for("patient_dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def register():
    if get_current_patient():
        return redirect(url_for("patient_dashboard"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        error = validate_registration(name, email, phone, password, confirm)
        if error:
            flash(error, "danger")
            return redirect(url_for("register"))

        user = User(name=name, email=email, phone=phone, role="patient")
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        flash("Registration successful! Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if get_current_patient():
        return redirect(url_for("patient_dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email, role="patient").first()
        if user and user.check_password(password):
            session["patient_id"] = user.id
            flash(f"Welcome back, {user.name}!", "success")
            return redirect(url_for("patient_dashboard"))
        flash("Invalid email or password.", "danger")
        return redirect(url_for("login"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("patient_id", None)
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


@app.route("/profile", methods=["GET", "POST"])
@patient_required
def profile():
    user = get_current_patient()
    if request.method == "POST":
        handle_profile_update(user)
        return redirect(url_for("profile"))
    return render_template("profile.html", profile_action=url_for("profile"))


# ---------------------------------------------------------------------------
# Driver portal -- a separate, unlinked entry point with its own session.
# Nothing on the public patient site links here; the URL is shared with
# drivers directly.
# ---------------------------------------------------------------------------

@app.route("/driver/register", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def driver_register():
    if get_current_driver():
        return redirect(url_for("driver_dashboard"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        error = validate_registration(name, email, phone, password, confirm)
        if error:
            flash(error, "danger")
            return redirect(url_for("driver_register"))

        user = User(name=name, email=email, phone=phone, role="driver")
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        vehicle_number = request.form.get("vehicle_number", "").strip()
        if not vehicle_number:
            vehicle_number = f"AMB-{user.id:04d}"
        if Ambulance.query.filter_by(vehicle_number=vehicle_number).first():
            vehicle_number = f"{vehicle_number}-{user.id}"
        ambulance = Ambulance(vehicle_number=vehicle_number, driver_id=user.id, status="offline")
        db.session.add(ambulance)
        db.session.commit()

        flash("Registration successful! Please log in.", "success")
        return redirect(url_for("driver_login"))

    return render_template("driver/register.html")


@app.route("/driver/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def driver_login():
    if get_current_driver():
        return redirect(url_for("driver_dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email, role="driver").first()
        if user and user.check_password(password):
            session["driver_id"] = user.id
            flash(f"Welcome back, {user.name}!", "success")
            return redirect(url_for("driver_dashboard"))
        flash("Invalid email or password.", "danger")
        return redirect(url_for("driver_login"))
    return render_template("driver/login.html")


@app.route("/driver/logout")
def driver_logout():
    session.pop("driver_id", None)
    flash("You have been logged out.", "info")
    return redirect(url_for("driver_login"))


@app.route("/driver/profile", methods=["GET", "POST"])
@driver_required
def driver_profile():
    user = get_current_driver()
    if request.method == "POST":
        handle_profile_update(user)
        return redirect(url_for("driver_profile"))
    return render_template("profile.html", profile_action=url_for("driver_profile"))


# ---------------------------------------------------------------------------
# Admin portal -- a separate, unlinked entry point with its own session. No
# self-service sign-up; admin accounts are only provisioned via
# seed_database() or directly in the database. Share this URL with
# administrators out of band.
# ---------------------------------------------------------------------------

@app.route("/admin/login", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def admin_login():
    if get_current_admin():
        return redirect(url_for("admin_dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email, role="admin").first()
        if user and user.check_password(password):
            session["admin_id"] = user.id
            flash(f"Welcome back, {user.name}!", "success")
            return redirect(url_for("admin_dashboard"))
        flash("Invalid email or password.", "danger")
        return redirect(url_for("admin_login"))
    return render_template("admin/login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_id", None)
    flash("You have been logged out.", "info")
    return redirect(url_for("admin_login"))


@app.route("/admin/profile", methods=["GET", "POST"])
@admin_required
def admin_profile():
    user = get_current_admin()
    if request.method == "POST":
        handle_profile_update(user)
        return redirect(url_for("admin_profile"))
    return render_template("profile.html", profile_action=url_for("admin_profile"))


# ---------------------------------------------------------------------------
# Patient routes
# ---------------------------------------------------------------------------

@app.route("/patient/dashboard")
@patient_required
def patient_dashboard():
    patient = get_current_patient()
    active_booking = (
        Booking.query.filter_by(patient_id=patient.id)
        .filter(Booking.status.in_(["pending", "requested", "accepted", "enroute"]))
        .order_by(Booking.created_at.desc())
        .first()
    )
    recent = (
        Booking.query.filter_by(patient_id=patient.id)
        .order_by(Booking.created_at.desc())
        .limit(5)
        .all()
    )
    return render_template("patient/dashboard.html", active_booking=active_booking, recent=recent)


@app.route("/patient/book", methods=["GET", "POST"])
@patient_required
def patient_book():
    patient = get_current_patient()
    existing = (
        Booking.query.filter_by(patient_id=patient.id)
        .filter(Booking.status.in_(["pending", "requested", "accepted", "enroute"]))
        .first()
    )
    if existing:
        flash("You already have an active booking in progress.", "warning")
        return redirect(url_for("patient_track", booking_id=existing.id))

    if request.method == "POST":
        try:
            lat = float(request.form.get("lat"))
            lng = float(request.form.get("lng"))
        except (TypeError, ValueError):
            flash("Location is required. Please allow location access or enter it manually.", "danger")
            return redirect(url_for("patient_book"))

        address = request.form.get("address", "").strip()
        notes = request.form.get("notes", "").strip()
        emergency_type = request.form.get("emergency_type", "general")
        if emergency_type not in EMERGENCY_TYPES:
            emergency_type = "general"

        booking = Booking(
            patient_id=patient.id,
            pickup_lat=lat,
            pickup_lng=lng,
            address=address,
            notes=notes,
            emergency_type=emergency_type,
            status="pending",
        )
        db.session.add(booking)
        db.session.commit()

        ambulance = dispatch_booking(booking)
        if ambulance:
            flash(f"Ambulance {ambulance.vehicle_number} has been notified and is on the way!", "success")
        else:
            flash("No ambulances are available right now. We will keep searching.", "warning")

        return redirect(url_for("patient_track", booking_id=booking.id))

    return render_template("patient/book.html")


@app.route("/patient/track/<int:booking_id>")
@patient_required
def patient_track(booking_id):
    patient = get_current_patient()
    booking = Booking.query.get_or_404(booking_id)
    if booking.patient_id != patient.id:
        abort(403)
    return render_template("patient/track.html", booking=booking)


@app.route("/patient/cancel/<int:booking_id>", methods=["POST"])
@patient_required
def patient_cancel(booking_id):
    patient = get_current_patient()
    booking = Booking.query.get_or_404(booking_id)
    if booking.patient_id != patient.id:
        abort(403)
    if booking.status in ("pending", "requested", "accepted", "no_ambulance"):
        freed_ambulance = booking.ambulance
        if freed_ambulance:
            freed_ambulance.status = "available"
            if freed_ambulance.driver_id:
                notify(freed_ambulance.driver_id, f"Booking #{booking.id} was cancelled by the patient.")
        booking.status = "cancelled"
        db.session.commit()
        if freed_ambulance:
            try_serve_waiting_booking(freed_ambulance)
        flash("Booking cancelled.", "info")
    else:
        flash("This booking can no longer be cancelled.", "warning")
    return redirect(url_for("patient_dashboard"))


@app.route("/patient/history")
@patient_required
def patient_history():
    patient = get_current_patient()
    page = request.args.get("page", 1, type=int)
    pagination = (
        Booking.query.filter_by(patient_id=patient.id)
        .order_by(Booking.created_at.desc())
        .paginate(page=page, per_page=15, error_out=False)
    )
    return render_template("patient/history.html", bookings=pagination.items, pagination=pagination)


@app.route("/patient/rate/<int:booking_id>", methods=["POST"])
@patient_required
def patient_rate_booking(booking_id):
    patient = get_current_patient()
    booking = Booking.query.get_or_404(booking_id)
    if booking.patient_id != patient.id:
        abort(403)
    if booking.status != "completed":
        flash("Only completed trips can be rated.", "warning")
        return redirect(url_for("patient_track", booking_id=booking.id))
    if booking.rating:
        flash("You've already rated this trip.", "info")
        return redirect(url_for("patient_track", booking_id=booking.id))

    try:
        rating = int(request.form.get("rating", 0))
    except (TypeError, ValueError):
        rating = 0
    if rating < 1 or rating > 5:
        flash("Please select a rating between 1 and 5 stars.", "danger")
        return redirect(url_for("patient_track", booking_id=booking.id))

    booking.rating = rating
    booking.feedback = request.form.get("feedback", "").strip()[:255]
    db.session.commit()
    flash("Thanks for your feedback!", "success")
    return redirect(url_for("patient_track", booking_id=booking.id))


# ---------------------------------------------------------------------------
# Driver routes
# ---------------------------------------------------------------------------

@app.route("/driver/dashboard")
@driver_required
def driver_dashboard():
    driver = get_current_driver()
    ambulance = driver.ambulance
    incoming = None
    active = None
    if ambulance:
        incoming = Booking.query.filter_by(ambulance_id=ambulance.id, status="requested").first()
        active = Booking.query.filter_by(ambulance_id=ambulance.id).filter(
            Booking.status.in_(["accepted", "enroute"])
        ).first()
    history = []
    if ambulance:
        history = (
            Booking.query.filter_by(ambulance_id=ambulance.id, status="completed")
            .order_by(Booking.completed_at.desc())
            .limit(10)
            .all()
        )
    return render_template(
        "driver/dashboard.html", ambulance=ambulance, incoming=incoming, active=active, history=history
    )


@app.route("/driver/toggle_availability", methods=["POST"])
@driver_required
def driver_toggle_availability():
    ambulance = get_current_driver().ambulance
    if not ambulance:
        flash("No ambulance is linked to your account. Contact the admin.", "danger")
        return redirect(url_for("driver_dashboard"))
    if ambulance.status == "offline":
        ambulance.status = "available"
        flash("You are now online and available for requests.", "success")
        db.session.commit()
        try_serve_waiting_booking(ambulance)
    elif ambulance.status == "available":
        ambulance.status = "offline"
        flash("You are now offline.", "info")
        db.session.commit()
    else:
        flash("You cannot change availability while on an active trip.", "warning")
    return redirect(url_for("driver_dashboard"))


@app.route("/driver/update_location", methods=["POST"])
@driver_required
def driver_update_location():
    ambulance = get_current_driver().ambulance
    if not ambulance:
        return jsonify({"error": "no ambulance"}), 400
    data = request.get_json(silent=True) or {}
    try:
        ambulance.lat = float(data.get("lat"))
        ambulance.lng = float(data.get("lng"))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid coordinates"}), 400
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/driver/accept/<int:booking_id>", methods=["POST"])
@driver_required
def driver_accept(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    ambulance = get_current_driver().ambulance
    if not ambulance or booking.ambulance_id != ambulance.id or booking.status != "requested":
        flash("This request is no longer available.", "warning")
        return redirect(url_for("driver_dashboard"))

    booking.status = "accepted"
    booking.accepted_at = datetime.utcnow()
    notify(booking.patient_id, f"Ambulance {ambulance.vehicle_number} accepted your request and is on the way.")
    db.session.commit()
    flash("Request accepted. Drive safely.", "success")
    return redirect(url_for("driver_dashboard"))


@app.route("/driver/reject/<int:booking_id>", methods=["POST"])
@driver_required
def driver_reject(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    ambulance = get_current_driver().ambulance
    if not ambulance or booking.ambulance_id != ambulance.id or booking.status != "requested":
        flash("This request is no longer available.", "warning")
        return redirect(url_for("driver_dashboard"))

    ambulance.status = "available"
    booking.add_rejected(ambulance.id)
    booking.ambulance_id = None
    booking.status = "pending"
    db.session.commit()

    new_ambulance = dispatch_booking(booking)
    if new_ambulance:
        flash("Request passed to the next nearest ambulance.", "info")
    else:
        flash("Request declined. No other ambulance is available right now.", "warning")
    try_serve_waiting_booking(ambulance)
    return redirect(url_for("driver_dashboard"))


@app.route("/driver/start_trip/<int:booking_id>", methods=["POST"])
@driver_required
def driver_start_trip(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    ambulance = get_current_driver().ambulance
    if not ambulance or booking.ambulance_id != ambulance.id or booking.status != "accepted":
        flash("Invalid action.", "warning")
        return redirect(url_for("driver_dashboard"))
    booking.status = "enroute"
    notify(booking.patient_id, "Your ambulance is en route to your location.")
    db.session.commit()
    flash("Trip marked as en route.", "success")
    return redirect(url_for("driver_dashboard"))


@app.route("/driver/complete/<int:booking_id>", methods=["POST"])
@driver_required
def driver_complete(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    ambulance = get_current_driver().ambulance
    if not ambulance or booking.ambulance_id != ambulance.id or booking.status not in ("accepted", "enroute"):
        flash("Invalid action.", "warning")
        return redirect(url_for("driver_dashboard"))

    booking.status = "completed"
    booking.completed_at = datetime.utcnow()
    ambulance.status = "available"
    notify(booking.patient_id, "Your trip has been completed. Stay safe!")
    db.session.commit()
    flash("Trip completed successfully.", "success")
    try_serve_waiting_booking(ambulance)
    return redirect(url_for("driver_dashboard"))


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------

@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    avg_rating_raw = db.session.query(db.func.avg(Booking.rating)).filter(Booking.rating.isnot(None)).scalar()
    avg_rating = round(avg_rating_raw, 1) if avg_rating_raw is not None else None

    stats = {
        "total_patients": User.query.filter_by(role="patient").count(),
        "total_drivers": User.query.filter_by(role="driver").count(),
        "total_ambulances": Ambulance.query.count(),
        "available_ambulances": Ambulance.query.filter_by(status="available").count(),
        "busy_ambulances": Ambulance.query.filter_by(status="busy").count(),
        "offline_ambulances": Ambulance.query.filter_by(status="offline").count(),
        "total_bookings": Booking.query.count(),
        "active_bookings": Booking.query.filter(
            Booking.status.in_(["pending", "requested", "accepted", "enroute"])
        ).count(),
        "completed_bookings": Booking.query.filter_by(status="completed").count(),
        "avg_rating": avg_rating,
    }
    recent_bookings = Booking.query.order_by(Booking.created_at.desc()).limit(8).all()
    return render_template("admin/dashboard.html", stats=stats, recent_bookings=recent_bookings)


@app.route("/admin/ambulances", methods=["GET", "POST"])
@admin_required
def admin_ambulances():
    if request.method == "POST":
        vehicle_number = request.form.get("vehicle_number", "").strip()
        driver_id = request.form.get("driver_id") or None
        vehicle_type = request.form.get("vehicle_type", "BLS")
        if vehicle_type not in VEHICLE_TYPES:
            vehicle_type = "BLS"

        driver = User.query.filter_by(id=driver_id, role="driver").first() if driver_id else None

        if not vehicle_number:
            flash("Vehicle number is required.", "danger")
        elif Ambulance.query.filter_by(vehicle_number=vehicle_number).first():
            flash("A vehicle with this number already exists.", "danger")
        elif driver_id and not driver:
            flash("Selected driver could not be found.", "danger")
        elif driver and driver.ambulance:
            # Belt-and-suspenders: the dropdown is only ever populated with
            # unassigned drivers, but a stale page or a direct POST could
            # still submit a driver who already has a vehicle. Ambulance.driver_id
            # is unique at the database level, so letting this through would
            # otherwise crash the request with an unhandled IntegrityError.
            flash(f"{driver.name} is already assigned to {driver.ambulance.vehicle_number}.", "danger")
        else:
            ambulance = Ambulance(
                vehicle_number=vehicle_number,
                vehicle_type=vehicle_type,
                driver_id=driver.id if driver else None,
                status="offline",
            )
            db.session.add(ambulance)
            try:
                db.session.commit()
                flash("Ambulance added.", "success")
            except IntegrityError:
                db.session.rollback()
                flash("Couldn't add that ambulance — please check the details and try again.", "danger")
        return redirect(url_for("admin_ambulances"))

    ambulances = Ambulance.query.order_by(Ambulance.id).all()
    unassigned_drivers = User.query.filter_by(role="driver").filter(
        ~User.id.in_([a.driver_id for a in ambulances if a.driver_id])
    ).all()
    return render_template("admin/ambulances.html", ambulances=ambulances, unassigned_drivers=unassigned_drivers)


@app.route("/admin/ambulances/delete/<int:ambulance_id>", methods=["POST"])
@admin_required
def admin_delete_ambulance(ambulance_id):
    ambulance = Ambulance.query.get_or_404(ambulance_id)
    if ambulance.status == "busy":
        flash("Cannot remove an ambulance that is currently on a trip.", "danger")
    else:
        db.session.delete(ambulance)
        db.session.commit()
        flash("Ambulance removed.", "info")
    return redirect(url_for("admin_ambulances"))


@app.route("/admin/users")
@admin_required
def admin_users():
    patients = User.query.filter_by(role="patient").order_by(User.created_at.desc()).all()
    drivers = User.query.filter_by(role="driver").order_by(User.created_at.desc()).all()
    return render_template("admin/users.html", patients=patients, drivers=drivers)


@app.route("/admin/bookings")
@admin_required
def admin_bookings():
    status_filter = request.args.get("status")
    page = request.args.get("page", 1, type=int)
    query = Booking.query
    if status_filter:
        query = query.filter_by(status=status_filter)
    pagination = query.order_by(Booking.created_at.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template(
        "admin/bookings.html", bookings=pagination.items, pagination=pagination, status_filter=status_filter
    )


# ---------------------------------------------------------------------------
# Admin "simulate driver" controls — lets an admin drive a booking through
# its full lifecycle for testing/demo purposes, without needing to log in
# separately as the assigned driver. Mirrors the driver_* routes above.
# ---------------------------------------------------------------------------

@app.route("/admin/simulate/accept/<int:booking_id>", methods=["POST"])
@admin_required
def admin_simulate_accept(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    ambulance = booking.ambulance
    if not ambulance or booking.status != "requested":
        flash("This booking isn't waiting on a driver response right now.", "warning")
        return redirect(url_for("admin_bookings"))
    booking.status = "accepted"
    booking.accepted_at = datetime.utcnow()
    notify(booking.patient_id, f"Ambulance {ambulance.vehicle_number} accepted your request and is on the way.")
    db.session.commit()
    flash(f"(Simulated) {ambulance.vehicle_number} accepted booking #{booking.id}.", "success")
    return redirect(url_for("admin_bookings"))


@app.route("/admin/simulate/reject/<int:booking_id>", methods=["POST"])
@admin_required
def admin_simulate_reject(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    ambulance = booking.ambulance
    if not ambulance or booking.status != "requested":
        flash("This booking isn't waiting on a driver response right now.", "warning")
        return redirect(url_for("admin_bookings"))
    ambulance.status = "available"
    booking.add_rejected(ambulance.id)
    booking.ambulance_id = None
    booking.status = "pending"
    db.session.commit()
    new_ambulance = dispatch_booking(booking)
    if new_ambulance:
        flash(f"(Simulated) Passed to the next nearest ambulance ({new_ambulance.vehicle_number}).", "info")
    else:
        flash("(Simulated) Declined. No other ambulance is available right now.", "warning")
    try_serve_waiting_booking(ambulance)
    return redirect(url_for("admin_bookings"))


@app.route("/admin/simulate/start/<int:booking_id>", methods=["POST"])
@admin_required
def admin_simulate_start(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.status != "accepted":
        flash("This booking isn't ready to start.", "warning")
        return redirect(url_for("admin_bookings"))
    booking.status = "enroute"
    notify(booking.patient_id, "Your ambulance is en route to your location.")
    db.session.commit()
    flash(f"(Simulated) Booking #{booking.id} marked en route.", "success")
    return redirect(url_for("admin_bookings"))


@app.route("/admin/simulate/complete/<int:booking_id>", methods=["POST"])
@admin_required
def admin_simulate_complete(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.status not in ("accepted", "enroute"):
        flash("This booking isn't active.", "warning")
        return redirect(url_for("admin_bookings"))
    booking.status = "completed"
    booking.completed_at = datetime.utcnow()
    freed_ambulance = booking.ambulance
    if freed_ambulance:
        freed_ambulance.status = "available"
    notify(booking.patient_id, "Your trip has been completed. Stay safe!")
    db.session.commit()
    flash(f"(Simulated) Booking #{booking.id} completed.", "success")
    if freed_ambulance:
        try_serve_waiting_booking(freed_ambulance)
    return redirect(url_for("admin_bookings"))


# ---------------------------------------------------------------------------
# Shared API (notifications + live status polling)
# ---------------------------------------------------------------------------

@app.route("/api/booking_status/<int:booking_id>")
@patient_required
def api_booking_status(booking_id):
    patient = get_current_patient()
    booking = Booking.query.get_or_404(booking_id)
    if booking.patient_id != patient.id:
        abort(403)
    return jsonify(booking.to_dict())


@app.route("/api/notifications")
def api_notifications():
    # Called via background fetch from whichever portal's dashboard is
    # currently open. The page tells us which portal it belongs to via
    # ?as=, since the request path itself is the same for all three.
    portal = request.args.get("as")
    if portal == "driver":
        user = get_current_driver()
    elif portal == "admin":
        user = get_current_admin()
    else:
        user = get_current_patient()

    if not user:
        return jsonify([])

    items = (
        Notification.query.filter_by(user_id=user.id, is_read=False)
        .order_by(Notification.created_at.desc())
        .all()
    )
    result = [{"id": n.id, "message": n.message, "created_at": n.created_at.strftime("%H:%M")} for n in items]
    for n in items:
        n.is_read = True
    db.session.commit()
    return jsonify(result)


@app.route("/api/driver_dashboard_state")
@driver_required
def api_driver_dashboard_state():
    ambulance = get_current_driver().ambulance
    if not ambulance:
        return jsonify({"has_ambulance": False})
    incoming = Booking.query.filter_by(ambulance_id=ambulance.id, status="requested").first()
    active = Booking.query.filter_by(ambulance_id=ambulance.id).filter(
        Booking.status.in_(["accepted", "enroute"])
    ).first()
    return jsonify({
        "has_ambulance": True,
        "status": ambulance.status,
        "incoming": incoming.to_dict() if incoming else None,
        "active": active.to_dict() if active else None,
    })


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", code=403, message="You don't have permission to view this page."), 403


@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="Page not found."), 404


@app.errorhandler(429)
def rate_limited(e):
    return render_template(
        "error.html", code=429, message="Too many attempts. Please wait a moment and try again."
    ), 429


@app.errorhandler(CSRFError)
def csrf_error(e):
    return render_template(
        "error.html", code=400,
        message="Your session expired or the form was submitted incorrectly. Please go back and try again.",
    ), 400


@app.errorhandler(500)
def internal_error(e):
    # db.session may be left in a failed transaction state after an
    # exception (e.g. a half-applied commit) -- roll it back so the *next*
    # request on this worker starts from a clean session instead of also
    # failing.
    db.session.rollback()
    app.logger.exception("Unhandled exception while processing %s %s", request.method, request.path)
    return render_template(
        "error.html", code=500, message="Something went wrong on our end. Please try again shortly.",
    ), 500


# ---------------------------------------------------------------------------
# CLI: seed the database with a default admin + demo data
# ---------------------------------------------------------------------------

def seed_database():
    if User.query.filter_by(role="admin").first():
        return

    admin_email = app.config["ADMIN_EMAIL"]
    admin_password = app.config["ADMIN_PASSWORD"]
    admin = User(name="System Admin", email=admin_email, phone="0300-0000000", role="admin")
    admin.set_password(admin_password)
    db.session.add(admin)

    if admin_password == "admin123":
        app.logger.warning(
            "Seeding the admin account with the default password. Set the ADMIN_PASSWORD "
            "environment variable to something private before this is reachable on the "
            "public internet."
        )

    if app.config["SEED_DEMO_DATA"]:
        demo_drivers = [
            ("Ali Khan", "ali.driver@ambulanceapp.com", "0301-1111111", "AMB-001", "ALS", 34.0083, 71.5789),
            ("Bilal Ahmed", "bilal.driver@ambulanceapp.com", "0302-2222222", "AMB-002", "BLS", 34.0151, 71.5249),
            ("Sara Malik", "sara.driver@ambulanceapp.com", "0303-3333333", "AMB-003", "BLS", 33.9990, 71.5429),
        ]
        for name, email, phone, vehicle, vehicle_type, lat, lng in demo_drivers:
            driver = User(name=name, email=email, phone=phone, role="driver")
            driver.set_password("driver123")
            db.session.add(driver)
            db.session.flush()
            ambulance = Ambulance(
                vehicle_number=vehicle, vehicle_type=vehicle_type, driver_id=driver.id,
                status="available", lat=lat, lng=lng,
            )
            db.session.add(ambulance)

        patient = User(name="Demo Patient", email="patient@ambulanceapp.com", phone="0304-4444444", role="patient")
        patient.set_password("patient123")
        db.session.add(patient)

    try:
        db.session.commit()
        app.logger.info("Seeded database with admin account%s.", " and demo data" if app.config["SEED_DEMO_DATA"] else "")
    except IntegrityError:
        # Another worker process seeded the database in the moment between
        # our check above and this commit (e.g. gunicorn starting multiple
        # workers at once, each running this at import time). That's fine --
        # someone won the race, so just back off instead of crashing.
        db.session.rollback()
        app.logger.info("Database was already seeded by another process.")


@app.cli.command("seed")
def seed_command():
    """Seed the database: flask --app app.py seed"""
    seed_database()


with app.app_context():
    db.create_all()
    seed_database()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=app.config["DEBUG"], host="0.0.0.0", port=port)

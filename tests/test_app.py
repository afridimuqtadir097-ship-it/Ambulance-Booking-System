"""
Core regression tests for MedRush.

Run with: pytest
(requires requirements.txt AND requirements-dev.txt installed)

These intentionally favor a handful of meaningful end-to-end checks
(register -> login -> book -> track, portal isolation, authorization) over
exhaustively testing every route, plus a couple of fast pure-logic unit
tests for the trickier helper functions.
"""
import itertools

import pytest

from app import haversine_km
from models import db, User, Ambulance, Booking

_counter = itertools.count(1)


def unique_email(prefix="user"):
    return f"{prefix}{next(_counter)}@example.com"


def register_patient(client, email, password="testpass123"):
    return client.post("/register", data={
        "name": "Test Patient", "email": email, "phone": "0300-0000001",
        "password": password, "confirm_password": password,
    })


def login_patient(client, email, password="testpass123"):
    return client.post("/login", data={"email": email, "password": password})


# ---------------------------------------------------------------------------
# Pure-logic unit tests -- no app/DB needed
# ---------------------------------------------------------------------------

def test_haversine_same_point_is_zero():
    assert haversine_km(34.0, 71.0, 34.0, 71.0) == pytest.approx(0.0, abs=1e-6)


def test_haversine_known_distance():
    # Islamabad to Lahore is roughly 275 km as the crow flies.
    dist = haversine_km(33.6844, 73.0479, 31.5497, 74.3436)
    assert 250 < dist < 300


def test_booking_rejected_list_round_trip():
    booking = Booking(patient_id=1, pickup_lat=0, pickup_lng=0)
    assert booking.rejected_list() == []
    booking.add_rejected(5)
    booking.add_rejected(9)
    assert booking.rejected_list() == [5, 9]


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------

def test_index_loads(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"MedRush" in resp.data


# ---------------------------------------------------------------------------
# Patient registration / login
# ---------------------------------------------------------------------------

def test_register_and_login(client):
    email = unique_email("patient")
    resp = register_patient(client, email)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/login")

    resp = login_patient(client, email)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/patient/dashboard")

    resp = client.get("/patient/dashboard")
    assert resp.status_code == 200


def test_register_rejects_short_password(client):
    email = unique_email("shortpw")
    resp = register_patient(client, email, password="short1")
    assert resp.status_code == 302
    resp = client.get(resp.headers["Location"])
    assert b"at least 8 characters" in resp.data.lower()


def test_register_rejects_mismatched_passwords(client):
    email = unique_email("mismatch")
    resp = client.post("/register", data={
        "name": "Test", "email": email, "phone": "0300-0000002",
        "password": "testpass123", "confirm_password": "different123",
    })
    assert resp.status_code == 302
    resp = client.get(resp.headers["Location"])
    assert b"do not match" in resp.data.lower()


def test_login_rejects_wrong_password(client):
    email = unique_email("wrongpw")
    register_patient(client, email)
    resp = login_patient(client, email, password="wrongpassword")
    assert resp.status_code == 302
    resp = client.get(resp.headers["Location"])
    assert b"invalid email or password" in resp.data.lower()


# ---------------------------------------------------------------------------
# Portal isolation -- patient / driver / admin sessions must not leak
# into one another.
# ---------------------------------------------------------------------------

def test_patient_session_cannot_reach_driver_portal(client):
    email = unique_email("isolation")
    register_patient(client, email)
    login_patient(client, email)

    resp = client.get("/driver/dashboard")
    assert resp.status_code == 302
    assert "/driver/login" in resp.headers["Location"]


def test_seeded_admin_login_uses_configured_password(client):
    # Confirms ADMIN_PASSWORD is actually wired through seed_database(),
    # instead of the old hardcoded "admin123".
    resp = client.post("/admin/login", data={
        "email": "admin@ambulanceapp.com", "password": "test-admin-pass",
    })
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/admin/dashboard")


# ---------------------------------------------------------------------------
# Booking flow -- each test creates its own isolated driver/ambulance at a
# distinctive location so it never depends on (or disturbs) the shared demo
# fleet seeded at app startup.
# ---------------------------------------------------------------------------

def _make_available_ambulance(flask_app, lat, lng):
    with flask_app.app_context():
        driver = User(name="Test Driver", email=unique_email("driver"), phone="0300-9999999", role="driver")
        driver.set_password("driverpass123")
        db.session.add(driver)
        db.session.flush()
        amb = Ambulance(
            vehicle_number=f"TEST-{driver.id}", driver_id=driver.id, status="available", lat=lat, lng=lng
        )
        db.session.add(amb)
        db.session.commit()
        return amb.id


def test_booking_dispatches_nearest_available_ambulance(client, flask_app):
    _make_available_ambulance(flask_app, lat=10.0, lng=10.0)

    email = unique_email("book")
    register_patient(client, email)
    login_patient(client, email)

    resp = client.post("/patient/book", data={
        "lat": "10.0", "lng": "10.0", "address": "Test Street", "emergency_type": "general",
    })
    assert resp.status_code == 302
    assert "/patient/track/" in resp.headers["Location"]

    with flask_app.app_context():
        user = User.query.filter_by(email=email).first()
        booking = Booking.query.filter_by(patient_id=user.id).first()
        assert booking is not None
        assert booking.status == "requested"
        assert booking.ambulance is not None
        assert booking.ambulance.vehicle_number.startswith("TEST-")
        booking_id = booking.id

    # Clean up: free the ambulance so it can't affect any other test.
    client.post(f"/patient/cancel/{booking_id}")


def test_patient_cannot_view_another_patients_booking(client, flask_app):
    _make_available_ambulance(flask_app, lat=20.0, lng=20.0)

    email_a = unique_email("owner")
    register_patient(client, email_a)
    login_patient(client, email_a)
    client.post("/patient/book", data={"lat": "20.0", "lng": "20.0", "emergency_type": "general"})

    with flask_app.app_context():
        owner = User.query.filter_by(email=email_a).first()
        booking = Booking.query.filter_by(patient_id=owner.id).first()
        booking_id = booking.id

    # A second, unrelated patient (a separate session/cookie jar) tries to
    # view it directly by guessing the URL.
    other_client = flask_app.test_client()
    email_b = unique_email("intruder")
    register_patient(other_client, email_b)
    login_patient(other_client, email_b)
    resp = other_client.get(f"/patient/track/{booking_id}")
    assert resp.status_code == 403

    client.post(f"/patient/cancel/{booking_id}")


def test_no_ambulance_booking_can_be_cancelled(client, flask_app):
    """Regression test: a booking that failed to find an ambulance used to
    be permanently stuck with no way to cancel it (see patient_cancel)."""
    with flask_app.app_context():
        available = Ambulance.query.filter_by(status="available").all()
        previous_statuses = {a.id: a.status for a in available}
        for a in available:
            a.status = "offline"
        db.session.commit()

    try:
        email = unique_email("noambulance")
        register_patient(client, email)
        login_patient(client, email)
        client.post("/patient/book", data={"lat": "5.0", "lng": "5.0", "emergency_type": "general"})

        with flask_app.app_context():
            user = User.query.filter_by(email=email).first()
            booking = Booking.query.filter_by(patient_id=user.id).first()
            assert booking.status == "no_ambulance"
            booking_id = booking.id

        resp = client.post(f"/patient/cancel/{booking_id}", follow_redirects=True)
        assert resp.status_code == 200
        assert b"booking cancelled" in resp.data.lower()

        with flask_app.app_context():
            booking = Booking.query.get(booking_id)
            assert booking.status == "cancelled"
    finally:
        with flask_app.app_context():
            for amb_id, status in previous_statuses.items():
                amb = Ambulance.query.get(amb_id)
                if amb:
                    amb.status = status
            db.session.commit()

from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

# Emergency categories used when a patient books an ambulance. Keys are stored
# on Booking.emergency_type; values are the human-readable labels + a colour
# tag used by the UI (matches the .tag-* classes in style.css).
EMERGENCY_TYPES = {
    "accident": {"label": "Accident / Trauma", "color": "red"},
    "cardiac": {"label": "Cardiac", "color": "purple"},
    "breathing": {"label": "Breathing Difficulty", "color": "blue"},
    "maternity": {"label": "Maternity", "color": "pink"},
    "general": {"label": "General Emergency", "color": "amber"},
}

VEHICLE_TYPES = {
    "BLS": "Basic Life Support",
    "ALS": "Advanced Life Support",
}


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(30), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="patient")  # patient, driver, admin
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    ambulance = db.relationship("Ambulance", backref="driver", uselist=False, foreign_keys="Ambulance.driver_id")
    bookings = db.relationship("Booking", backref="patient", foreign_keys="Booking.patient_id")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User {self.email} ({self.role})>"


class Ambulance(db.Model):
    __tablename__ = "ambulances"

    id = db.Column(db.Integer, primary_key=True)
    vehicle_number = db.Column(db.String(30), unique=True, nullable=False)
    vehicle_type = db.Column(db.String(10), nullable=False, default="BLS")  # BLS or ALS, see VEHICLE_TYPES
    driver_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=True, index=True)
    status = db.Column(db.String(20), nullable=False, default="offline", index=True)  # available, busy, offline
    lat = db.Column(db.Float, nullable=True)
    lng = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    bookings = db.relationship("Booking", backref="ambulance", foreign_keys="Booking.ambulance_id")

    def __repr__(self):
        return f"<Ambulance {self.vehicle_number}>"


class Booking(db.Model):
    __tablename__ = "bookings"

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    ambulance_id = db.Column(db.Integer, db.ForeignKey("ambulances.id"), nullable=True, index=True)

    pickup_lat = db.Column(db.Float, nullable=False)
    pickup_lng = db.Column(db.Float, nullable=False)
    address = db.Column(db.String(255), nullable=True)
    notes = db.Column(db.String(255), nullable=True)
    emergency_type = db.Column(db.String(30), nullable=False, default="general")  # see EMERGENCY_TYPES

    # pending -> requested -> accepted -> enroute -> completed  (or cancelled / no_ambulance)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    accepted_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)

    rejected_ambulance_ids = db.Column(db.String(255), nullable=True, default="")

    # Patient feedback, collected once a trip is completed.
    rating = db.Column(db.Integer, nullable=True)       # 1-5
    feedback = db.Column(db.String(255), nullable=True)

    def rejected_list(self):
        if not self.rejected_ambulance_ids:
            return []
        return [int(x) for x in self.rejected_ambulance_ids.split(",") if x]

    def add_rejected(self, ambulance_id):
        current = self.rejected_list()
        current.append(ambulance_id)
        self.rejected_ambulance_ids = ",".join(str(x) for x in current)

    def to_dict(self):
        return {
            "id": self.id,
            "status": self.status,
            "address": self.address,
            "emergency_type": self.emergency_type,
            "rating": self.rating,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M"),
            "accepted_at": self.accepted_at.strftime("%Y-%m-%d %H:%M") if self.accepted_at else None,
            "completed_at": self.completed_at.strftime("%Y-%m-%d %H:%M") if self.completed_at else None,
            "ambulance": {
                "vehicle_number": self.ambulance.vehicle_number,
                "lat": self.ambulance.lat,
                "lng": self.ambulance.lng,
                "driver_name": self.ambulance.driver.name if self.ambulance.driver else None,
                "driver_phone": self.ambulance.driver.phone if self.ambulance.driver else None,
            } if self.ambulance else None,
            "pickup_lat": self.pickup_lat,
            "pickup_lng": self.pickup_lng,
        }

    def __repr__(self):
        return f"<Booking {self.id} - {self.status}>"


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    message = db.Column(db.String(255), nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="notifications")

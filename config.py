import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "ambulance-booking-secret-key-change-in-production")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "ambulance.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Set FLASK_DEBUG=1 only for local development. Never enable debug mode in production.
    DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"

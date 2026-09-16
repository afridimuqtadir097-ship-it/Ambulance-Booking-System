import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def _normalize_db_url(url):
    """Some hosting providers (and older Heroku-style configs) hand out
    connection strings starting with 'postgres://', but SQLAlchemy 1.4+
    only recognizes the 'postgresql://' dialect prefix and raises
    NoSuchModuleError on the old form. Normalize it here so DATABASE_URL
    works regardless of which style the provider gives us."""
    if url and url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "ambulance-booking-secret-key-change-in-production")
    SQLALCHEMY_DATABASE_URI = _normalize_db_url(
        os.environ.get("DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "ambulance.db"))
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Set FLASK_DEBUG=1 only for local development. Never enable debug mode in production.
    DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"

    # Credentials used for the auto-seeded administrator account (see
    # seed_database() in app.py). Override these with real values via
    # environment variables before deploying anywhere publicly reachable --
    # the fallback password below is intentionally well-known and must not
    # be relied on outside of local development/demos.
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@ambulanceapp.com")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

    # Whether to auto-create the demo drivers + demo patient account on
    # startup, in addition to the admin account. Defaults on, since this is
    # what makes the seeded "Demo account" credentials shown on the login
    # pages work out of the box. Set SEED_DEMO_DATA=0 to disable if you ever
    # deploy this with real patient/driver signups and don't want the demo
    # accounts mixed in.
    SEED_DEMO_DATA = os.environ.get("SEED_DEMO_DATA", "1") == "1"

    # Standard Flask-Limiter storage. In-memory is fine for a single-process
    # deployment (e.g. Render's free tier); if you ever run multiple worker
    # processes/instances behind a load balancer, point this at a shared
    # store instead (e.g. redis://...) so rate limits are enforced globally
    # rather than per-process.
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")

"""
Shared pytest fixtures for the MedRush test suite.

Test-specific environment variables are set at import time (module level,
right below) so that config.py picks them up the *first* time app.py is
imported anywhere in the test session. Config's values are plain class
attributes evaluated once at import, not re-read per request, so this has
to happen before that first import -- pytest always imports conftest.py
before collecting sibling/child test files, which is what makes this safe.
"""
import os
import tempfile

import pytest

_db_fd, _DB_PATH = tempfile.mkstemp(suffix=".db")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["FLASK_DEBUG"] = "0"
os.environ["ADMIN_PASSWORD"] = "test-admin-pass"
os.environ["SEED_DEMO_DATA"] = "1"


@pytest.fixture(scope="session")
def flask_app():
    """The Flask app, imported exactly once for the whole test session
    against the temporary database created above. Importing app.py has the
    side effect of creating tables and seeding demo data (see the bottom of
    app.py) -- that's intentional; it's the same thing that happens on a
    real deploy, just pointed at a throwaway database here instead."""
    import app as app_module
    app_module.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    yield app_module.app
    os.close(_db_fd)
    os.unlink(_DB_PATH)


@pytest.fixture()
def client(flask_app):
    return flask_app.test_client()

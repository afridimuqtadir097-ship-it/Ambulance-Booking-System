# MedRush — Online Ambulance Booking App

![CI](https://github.com/afridimuqtadir097-ship-it/Ambulance-Booking-System/actions/workflows/ci.yml/badge.svg)

A full-stack Online Ambulance Booking web application built with **Python (Flask)**, matching the project proposal *"Online Ambulance Booking App Using Python"* by Muhammad Muqadir.

## Features (mapped to the proposal)

- **User Registration & Login** — secure authentication for Patients, Ambulance Drivers, and Admins (role-based access, hashed passwords, CSRF-protected forms, rate-limited against brute-force attempts).
- **Ambulance Booking Module** — patients pick an emergency type (Accident, Cardiac, Breathing Difficulty, Maternity, General), then book using browser GPS auto-detection (with a draggable map pin to fine-tune the pickup point).
- **Real-Time Dispatch** — the system automatically finds and assigns the *nearest available* ambulance using the Haversine distance formula; if a driver declines, it automatically re-dispatches to the next-nearest ambulance. If nothing is available, the request is automatically retried against the next ambulance that comes online, and the patient can cancel it at any time.
- **Driver Module** — drivers go online/offline, receive requests (colour-coded by emergency type), accept/pass, mark en route, and complete trips.
- **Ratings & Feedback** — once a trip is completed, patients rate it 1–5 stars with optional written feedback; ratings show up in the patient's history, the driver's trip log, and as a fleet-wide average on the admin dashboard.
- **Vehicle Types** — ambulances are tagged Basic Life Support (BLS) or Advanced Life Support (ALS), settable by the admin when adding a vehicle.
- **Admin Dashboard** — fleet stats, average rating, ambulance management (add/remove/assign, with vehicle type), user management, and full booking records with status filters and pagination.
- **Profile & Password Management** — every user can update their name/phone and change their password from a dedicated Profile page.
- **Booking History** — patients and drivers can see past trips, complete with emergency type and rating, paginated for long histories.
- **Notifications** — in-app real-time toast notifications (polled every few seconds) for booking status changes.
- **Live GPS Tracking** — patients see their assigned ambulance move on a live Leaflet/OpenStreetMap map; drivers share live location via the browser Geolocation API.
- **Colourful, responsive UI** — built with Bootstrap 5 and a custom themed stylesheet (gradient hero, colour-coded tags/badges, animated stat cards), works on desktop and mobile browsers.

## Tech Stack

- Backend: Python 3, Flask, Flask-SQLAlchemy, Flask-Login, Flask-WTF (CSRF protection), Flask-Limiter (rate limiting)
- Database: SQLite via SQLAlchemy ORM (zero-config; swap the `DATABASE_URL` env var for MySQL/Postgres if needed)
- Frontend: HTML5, CSS3, Bootstrap 5, Bootstrap Icons, vanilla JavaScript
- Maps/GPS: Leaflet.js + OpenStreetMap (no API key required)
- Testing/CI: pytest, GitHub Actions

## Setup

1. **Install Python 3.10+** if you don't already have it.
2. Open a terminal in this folder and create a virtual environment (recommended):
   ```
   python -m venv venv
   venv\Scripts\activate        (Windows)
   source venv/bin/activate     (Mac/Linux)
   ```
3. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
4. Run the app:
   ```
   python app.py
   ```
5. Open your browser at **http://127.0.0.1:5000**

The database (`ambulance.db`) and demo accounts are created automatically the first time you run the app — `db.create_all()` builds every table from `models.py` and `seed_database()` populates the demo accounts below. There's no `ambulance.db` checked in right now (see **Database** below), so your very first run will build one fresh, matching the current schema. If you already have an `ambulance.db` from before the schema-hardening pass described below, delete it once so the new indexes get created — it'll be rebuilt automatically (and reseeded) on the next run.

## Testing

A pytest suite covers the dispatch logic, the three portals' auth flows, session isolation between them, and booking authorization/cancellation edge cases:

```
pip install -r requirements.txt -r requirements-dev.txt
pytest -v
```

Tests run against a temporary throwaway SQLite database (never your real `ambulance.db`) and run automatically on every push via the GitHub Actions workflow in `.github/workflows/ci.yml` (see the badge at the top of this file).

## Database

MedRush uses **SQLAlchemy** as its ORM with four tables, defined in `models.py`:

| Table           | Purpose                                                                 |
|-----------------|--------------------------------------------------------------------------|
| `users`         | Patients, drivers, and admins — name, email, phone, hashed password, role |
| `ambulances`    | Vehicle number, type (BLS/ALS), linked driver, status, live lat/lng      |
| `bookings`      | Pickup location, emergency type, status lifecycle, timestamps, rating/feedback |
| `notifications` | In-app notifications per user                                            |

By default it runs on **SQLite** (`ambulance.db`, created automatically — nothing to install). To point it at a real database instead (Postgres, MySQL, etc.) for production, set the `DATABASE_URL` environment variable, e.g.:
```
DATABASE_URL=postgresql://user:password@host:5432/medrush
```
`config.py` also normalizes the older `postgres://` prefix some providers hand out into `postgresql://`, which is what SQLAlchemy 1.4+ actually expects.

No manual migration step is needed for a fresh setup — `db.create_all()` builds the schema on first run. If you change `models.py` later on a database that already has data you want to keep, use a migration tool such as `Flask-Migrate` (Alembic) rather than deleting the file — that's the natural next step if this project grows beyond a single SQLite file.

A backed-up copy of the previous SQLite file (from before ratings/emergency-type/vehicle-type were added) sits in `_unused/ambulance_old_schema_backup.db` in case you need to recover old data from it manually; it won't be picked up by the app automatically.

## Three Separate Portals

MedRush is split into three independent portals that don't link to or reveal each other:

| Portal  | Login URL         | Sign-up URL           | Linked from the public homepage? |
|---------|--------------------|-------------------------|-----------------------------------|
| Patient | `/login`           | `/register`             | Yes — this is the public-facing site |
| Driver  | `/driver/login`     | `/driver/register`      | No — share this URL with drivers directly |
| Admin   | `/admin/login`      | *(none — admin accounts are only created via `seed_database()` or directly in the database)* | No — share this URL with administrators directly |

Each login only authenticates accounts of the matching role — a driver's credentials won't work on `/login`, and vice versa — and each page only shows its own demo credentials (if any). There is no page anywhere in the UI that lists or links to the other two portals.

**Independent sessions:** each portal also keeps its own login session, stored under its own key (`patient_id` / `driver_id` / `admin_id`) rather than one shared login. Practically this means:
- Logging into one portal never logs you out of, or overwrites, another — you can be logged into the patient, driver, *and* admin portals at the same time in the same browser.
- Visiting `/driver/login` while you're logged into the patient portal shows the driver login form as normal, instead of bouncing you back to the patient dashboard.
- `app.py` implements this with `get_current_patient()` / `get_current_driver()` / `get_current_admin()` helpers and matching `patient_required` / `driver_required` / `admin_required` decorators, instead of Flask-Login's single global `current_user` session. Each portal also has its own `/profile` and `/logout` route (`/driver/profile`, `/driver/logout`, `/admin/profile`, `/admin/logout`) for the same reason.
- Templates still use `current_user` exactly as before (via a `@app.context_processor`) — it just now resolves to whichever portal's session matches the current URL.

## Demo Accounts (seeded automatically)

| Role    | Email                          | Password   | Login URL       | Notes        |
|---------|---------------------------------|------------|------------------|--------------|
| Admin   | admin@ambulanceapp.com          | admin123 *(unless `ADMIN_PASSWORD` is set — see Security below)* | `/admin/login`   |              |
| Driver  | ali.driver@ambulanceapp.com     | driver123  | `/driver/login`  | ALS vehicle  |
| Driver  | bilal.driver@ambulanceapp.com   | driver123  | `/driver/login`  | BLS vehicle  |
| Driver  | sara.driver@ambulanceapp.com    | driver123  | `/driver/login`  | BLS vehicle  |
| Patient | patient@ambulanceapp.com        | patient123 | `/login`         |              |

You can also register new patient accounts from `/register`, or new driver accounts from `/driver/register`. Set `SEED_DEMO_DATA=0` if you ever want to disable auto-creating the demo drivers/patient (the admin account is always seeded, so there's always a way in).

## Trying it out (recommended flow)

1. Go to `/driver/login` and log in as a **driver** (e.g. `ali.driver@ambulanceapp.com`), click **"Go Online"**, and allow location access.
2. In another (private/incognito) tab or browser, go to `/login` and log in as the **patient** account, go to **Book Ambulance**, pick an emergency type, allow location access, and submit a request.
3. Watch the driver tab — a new request appears automatically (polled every 5s), colour-tagged by emergency type. Accept it.
4. Watch the patient's tracking page update live through Requested → Accepted → En Route → Arrived.
5. Complete the trip as the driver, then rate it as the patient — the star rating appears in both dashboards and on the admin's fleet average.
6. Go to `/admin/login` to see the fleet, users, and booking records update in real time.

**Tip:** if you don't want to juggle multiple browser tabs/logins, log in as admin and use the "Simulate Accept / Start / Complete" buttons on the Bookings page instead of step 1–3 above — see the Admin Dashboard section below.

## Project Structure

```
my project__2/
├── app.py                   # Flask app, routes, dispatch logic, CSRF/rate-limit setup, error handlers
├── models.py                 # SQLAlchemy models + EMERGENCY_TYPES / VEHICLE_TYPES
├── config.py                  # App configuration (SECRET_KEY, DATABASE_URL, admin creds, rate-limit storage)
├── requirements.txt
├── requirements-dev.txt       # pytest, only needed to run the test suite
├── pytest.ini
├── tests/
│   ├── conftest.py            # test fixtures (isolated temp SQLite DB per test run)
│   └── test_app.py            # auth, dispatch, portal isolation, authorization tests
├── .github/workflows/ci.yml   # runs the test suite on every push/PR
├── Procfile                   # gunicorn entrypoint for hosting platforms
├── render.yaml                 # Render "Blueprint" — one-click free deploy config
├── .python-version             # pins the Python version for hosting platforms
├── .env.example                # copy to .env for local secrets
├── .gitignore
├── LICENSE                      # MIT
├── static/
│   ├── css/style.css          # themed, colour-coded stylesheet
│   ├── js/main.js
│   └── favicon.svg
└── templates/
    ├── auth_base.html      # minimal shared layout for all login/register pages (no cross-portal links)
    ├── base.html, index.html, error.html, profile.html, _pagination.html
    ├── login.html, register.html          # patient portal
    ├── patient/  (dashboard, book, track, history)
    ├── driver/   (login, register, dashboard)
    └── admin/    (login, dashboard, ambulances, users, bookings)
```

## Configuration

Copy `.env.example` to `.env` and adjust values as needed (or set the same variables directly in your hosting platform):

| Variable                | Purpose                                             | Default                        |
|--------------------------|------------------------------------------------------|---------------------------------|
| `SECRET_KEY`             | Flask session/CSRF signing key — **must** be changed in production | dev placeholder value |
| `DATABASE_URL`           | SQLAlchemy connection string                        | local `ambulance.db` SQLite file |
| `FLASK_DEBUG`            | `1` enables debug mode (local dev only)             | `0` (off)                      |
| `PORT`                   | Port the app listens on                             | `5000`                         |
| `ADMIN_EMAIL`            | Email for the auto-seeded admin account             | `admin@ambulanceapp.com`       |
| `ADMIN_PASSWORD`         | Password for the auto-seeded admin account — **change this before deploying publicly** | `admin123` |
| `SEED_DEMO_DATA`         | `0` disables auto-creating demo drivers/patient      | `1` (on)                       |
| `RATELIMIT_STORAGE_URI`  | Flask-Limiter backend — only matters with >1 worker/instance | `memory://` |

Generate a strong secret key with:
```
python -c "import secrets; print(secrets.token_hex(32))"
```

## Security

A few things worth knowing before pointing this at the public internet:

- **Change `ADMIN_PASSWORD`.** The auto-seeded admin account uses a well-known default password unless you override it via the `ADMIN_PASSWORD` environment variable. `seed_database()` logs a warning on startup if you haven't. This is the single most important thing to do before a real deployment.
- **Login/registration is rate-limited** (Flask-Limiter) on all five auth endpoints across the three portals, to slow down brute-force password guessing.
- **Passwords** are hashed with Werkzeug's `generate_password_hash` (never stored in plain text) and must be at least 8 characters.
- **CSRF protection** (Flask-WTF) is enabled globally; every state-changing form includes a token, and a failed/missing token now shows a friendly error page instead of a raw 400.
- **Authorization checks** ensure a patient can only view/cancel their *own* bookings (`abort(403)` otherwise) — verified by the test suite.
- **SQLite on Render's free tier is ephemeral** (see Deployment below) — fine for a demo, not for anything you need to persist. Point `DATABASE_URL` at a real Postgres instance if that matters to you.

## Deployment (free, on Render)

A `Procfile` (`web: gunicorn --bind 0.0.0.0:$PORT app:app`) and a `render.yaml` blueprint are included, so this repo deploys to [Render](https://render.com)'s free web service tier with almost no manual setup.

1. Push this repo to GitHub.
2. On [render.com](https://render.com), sign up/log in, then click **New +** → **Blueprint**, and pick this GitHub repo. Render reads `render.yaml` and pre-fills the service (free plan, build command, start command, a random `SECRET_KEY`). Click **Apply**.
   - Alternatively: **New +** → **Web Service** → pick the repo → set Build Command `pip install -r requirements.txt` and Start Command `gunicorn --bind 0.0.0.0:$PORT app:app` manually.
3. Before or right after the first deploy, set an `ADMIN_PASSWORD` environment variable in the Render dashboard (see Security above) — `render.yaml` doesn't set one for you.
4. Wait for the build/deploy logs to finish, then open the `*.onrender.com` URL Render gives you.

**Free-tier trade-offs to know about:**
- The service spins down after ~15 minutes of no traffic; the next visit takes ~30–60s to wake back up. Normal for a free demo, not something to fix.
- The filesystem is ephemeral, so the default SQLite database (`ambulance.db`) resets on every deploy or restart — you'll get a clean slate with the seeded demo accounts each time, but any bookings created in between are lost. Fine for a portfolio/demo link; if you need data to persist, point `DATABASE_URL` at an external managed Postgres instance (e.g. Neon or Supabase both have a free tier) instead of relying on SQLite — no code changes needed, `config.py` already reads `DATABASE_URL`.
- Render's Postgres add-on is no longer free, which is why an external Postgres provider is the free path if you want persistence.

On Windows/IIS or plain WSGI hosts, `waitress` (already in `requirements.txt`) is a good alternative for non-Render use:
```
waitress-serve --port=5000 app:app
```

## Notes

- `.gitignore` already excludes `venv/`, `__pycache__/`, `*.db`, and `.env` — don't commit these.
- A `_unused/` folder holds stray/duplicate files found during cleanup (unused template duplicates, a stray `package-lock.json`, an empty `mnt/` folder, and the pre-upgrade `ambulance.db`). Nothing in the app references them; delete the folder whenever you like.
- All state-changing forms are protected with Flask-WTF CSRF tokens. If you add a new `<form method="POST">` anywhere, remember to include `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">` inside it (or add `X-CSRFToken` header for JS `fetch` calls).
- Logging goes through `app.logger` (timestamped, leveled) rather than stray `print()` calls, so a host's log viewer (e.g. Render's Logs tab) shows something useful when things go wrong — unhandled exceptions are logged with a full traceback by the 500 error handler.
- Licensed under MIT — see `LICENSE`.

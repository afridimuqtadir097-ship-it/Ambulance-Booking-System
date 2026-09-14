"""
One-time migration script.

Copies any accounts / ambulances / bookings / notifications that only exist in
the pre-upgrade database (_unused/ambulance_old_schema_backup.db) into the
current database (ambulance.db). Anything already present (matched by email
for users, vehicle_number for ambulances) is skipped, so it's safe to run
more than once.

Usage (with your venv activated, from the project folder):
    python migrate_old_data.py

Run this AFTER you've started the app at least once with `python app.py`
(so ambulance.db exists with the new schema), and while the app is NOT
running (stop it with Ctrl+C first) to avoid a locked database file.
"""
import os
import sqlite3

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
OLD_DB = os.path.join(BASE_DIR, "_unused", "ambulance_old_schema_backup.db")
NEW_DB = os.path.join(BASE_DIR, "ambulance.db")


def main():
    if not os.path.exists(OLD_DB):
        print(f"Old database not found at {OLD_DB} -- nothing to migrate.")
        return
    if not os.path.exists(NEW_DB):
        print(f"New database not found at {NEW_DB}.")
        print("Run `python app.py` once first (then stop it with Ctrl+C), then re-run this script.")
        return

    old = sqlite3.connect(OLD_DB)
    old.row_factory = sqlite3.Row
    new = sqlite3.connect(NEW_DB)
    new.row_factory = sqlite3.Row

    old_cur = old.cursor()
    new_cur = new.cursor()

    # ---- Users --------------------------------------------------------
    old_users = old_cur.execute("SELECT * FROM users").fetchall()
    id_map = {}  # old user id -> new user id
    migrated_users = 0

    for u in old_users:
        existing = new_cur.execute("SELECT id FROM users WHERE email = ?", (u["email"],)).fetchone()
        if existing:
            id_map[u["id"]] = existing["id"]
            continue
        new_cur.execute(
            """INSERT INTO users (name, email, phone, password_hash, role, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (u["name"], u["email"], u["phone"], u["password_hash"], u["role"], u["created_at"]),
        )
        id_map[u["id"]] = new_cur.lastrowid
        migrated_users += 1

    new.commit()
    print(f"Users migrated: {migrated_users} (skipped {len(old_users) - migrated_users} already present)")

    # ---- Ambulances -----------------------------------------------------
    old_ambulances = old_cur.execute("SELECT * FROM ambulances").fetchall()
    amb_id_map = {}  # old ambulance id -> new ambulance id
    migrated_ambulances = 0

    for a in old_ambulances:
        existing = new_cur.execute(
            "SELECT id FROM ambulances WHERE vehicle_number = ?", (a["vehicle_number"],)
        ).fetchone()
        if existing:
            amb_id_map[a["id"]] = existing["id"]
            continue
        new_driver_id = id_map.get(a["driver_id"]) if a["driver_id"] else None
        new_cur.execute(
            """INSERT INTO ambulances (vehicle_number, vehicle_type, driver_id, status, lat, lng, created_at)
               VALUES (?, 'BLS', ?, ?, ?, ?, ?)""",
            (a["vehicle_number"], new_driver_id, a["status"], a["lat"], a["lng"], a["created_at"]),
        )
        amb_id_map[a["id"]] = new_cur.lastrowid
        migrated_ambulances += 1

    new.commit()
    print(f"Ambulances migrated: {migrated_ambulances} (skipped {len(old_ambulances) - migrated_ambulances} already present)")

    # ---- Bookings -------------------------------------------------------
    old_bookings = old_cur.execute("SELECT * FROM bookings").fetchall()
    migrated_bookings = 0
    skipped_bookings = 0

    for b in old_bookings:
        new_patient_id = id_map.get(b["patient_id"])
        if new_patient_id is None:
            skipped_bookings += 1
            continue  # patient wasn't migrated for some reason, skip orphan
        new_ambulance_id = amb_id_map.get(b["ambulance_id"]) if b["ambulance_id"] else None

        # Avoid duplicating a booking we've already migrated (match on patient + created_at)
        existing = new_cur.execute(
            "SELECT id FROM bookings WHERE patient_id = ? AND created_at = ?",
            (new_patient_id, b["created_at"]),
        ).fetchone()
        if existing:
            skipped_bookings += 1
            continue

        new_cur.execute(
            """INSERT INTO bookings
               (patient_id, ambulance_id, pickup_lat, pickup_lng, address, notes,
                emergency_type, status, created_at, accepted_at, completed_at,
                rejected_ambulance_ids, rating, feedback)
               VALUES (?, ?, ?, ?, ?, ?, 'general', ?, ?, ?, ?, ?, NULL, NULL)""",
            (
                new_patient_id, new_ambulance_id, b["pickup_lat"], b["pickup_lng"],
                b["address"], b["notes"], b["status"], b["created_at"],
                b["accepted_at"], b["completed_at"], b["rejected_ambulance_ids"],
            ),
        )
        migrated_bookings += 1

    new.commit()
    print(f"Bookings migrated: {migrated_bookings} (skipped {skipped_bookings})")
    print("  Note: migrated bookings default to emergency_type='general' since that field is new.")

    # ---- Notifications ----------------------------------------------------
    old_notifs = old_cur.execute("SELECT * FROM notifications").fetchall()
    migrated_notifs = 0
    skipped_notifs = 0

    for n in old_notifs:
        new_user_id = id_map.get(n["user_id"])
        if new_user_id is None:
            skipped_notifs += 1
            continue
        existing = new_cur.execute(
            "SELECT id FROM notifications WHERE user_id = ? AND message = ? AND created_at = ?",
            (new_user_id, n["message"], n["created_at"]),
        ).fetchone()
        if existing:
            skipped_notifs += 1
            continue
        new_cur.execute(
            "INSERT INTO notifications (user_id, message, is_read, created_at) VALUES (?, ?, ?, ?)",
            (new_user_id, n["message"], n["is_read"], n["created_at"]),
        )
        migrated_notifs += 1

    new.commit()
    print(f"Notifications migrated: {migrated_notifs} (skipped {skipped_notifs})")

    old.close()
    new.close()
    print("\nDone. Start the app again (python app.py) and log in with your old accounts.")


if __name__ == "__main__":
    main()

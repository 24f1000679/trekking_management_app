import os 
import sqlite3
from werkzeug.security import generate_password_hash
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trekking.db")



def open_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def build_schema():
    conn = open_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name     TEXT NOT NULL,
            email         TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL CHECK(role IN ('admin', 'staff', 'user')),
            phone_no      TEXT,
            account_status TEXT NOT NULL DEFAULT 'active',
            joined_on     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS expeditions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            title           TEXT NOT NULL,
            region          TEXT NOT NULL,
            difficulty_level TEXT NOT NULL CHECK(difficulty_level IN ('Easy', 'Moderate', 'Hard')),
            num_days        INTEGER NOT NULL,
            capacity        INTEGER NOT NULL,
            seats_left      INTEGER NOT NULL,
            guide_id        INTEGER,
            current_state   TEXT NOT NULL DEFAULT 'Pending'
                CHECK(current_state IN ('Pending', 'Approved', 'Open', 'Closed', 'Completed')),
            begin_date      TEXT,
            finish_date     TEXT,
            notes           TEXT,
            added_on        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (guide_id) REFERENCES accounts(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS reservations (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id        INTEGER NOT NULL,
            expedition_id     INTEGER NOT NULL,
            reserved_on       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reservation_state TEXT NOT NULL DEFAULT 'Booked'
                CHECK(reservation_state IN ('Booked', 'Cancelled', 'Completed')),
            FOREIGN KEY (account_id) REFERENCES accounts(id),
            FOREIGN KEY (expedition_id) REFERENCES expeditions(id)
        )
    """)
    conn.commit()

    admin_row = cur.execute("SELECT id FROM accounts WHERE role = 'admin' LIMIT 1").fetchone()
    if admin_row is None:
        cur.execute(
            """INSERT INTO accounts (full_name, email, password_hash, role, phone_no, account_status)
               VALUES (?, ?, ?, 'admin', ?, 'active')""",
            ("System Admin", "admin@trek.com", generate_password_hash("admin123"), "9999999999"),
        )
        conn.commit()

    conn.close()
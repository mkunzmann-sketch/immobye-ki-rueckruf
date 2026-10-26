"""Kleine SQLite-Datenbank für Anrufaufträge, Sperrliste und bereits verarbeitete Webhooks."""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER UNIQUE,
    client_id INTEGER NOT NULL,
    property_id INTEGER,
    broker_id INTEGER,
    phone TEXT,
    status TEXT NOT NULL,          -- pending | calling | done | skipped
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    last_call_id TEXT,
    call_started_at TEXT,
    outcome TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS jobs_client_prop ON jobs(client_id, property_id);

CREATE TABLE IF NOT EXISTS blocklist (phone TEXT PRIMARY KEY, client_id INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS processed (key TEXT PRIMARY KEY, created_at TEXT);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def iso(dt: datetime) -> str:
    """Einheitlich UTC speichern, damit Textvergleiche in SQLite stimmen."""
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s)


class DB:
    def __init__(self, path: str):
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.conn.executescript(SCHEMA)

    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def x(self, sql: str, args: tuple = ()) -> int:
        with self.lock:
            cur = self.conn.execute(sql, args)
            return cur.lastrowid

    # --- state ---------------------------------------------------------------
    def get_state(self, key: str) -> str | None:
        r = self.q("SELECT value FROM state WHERE key=?", (key,))
        return r[0]["value"] if r else None

    def set_state(self, key: str, value: str) -> None:
        self.x("INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    # --- jobs ----------------------------------------------------------------
    def job_exists(self, activity_id: int) -> bool:
        return bool(self.q("SELECT 1 FROM jobs WHERE activity_id=?", (activity_id,)))

    def talked_before(self, client_id: int, property_id: int | None) -> bool:
        """Wurde mit diesem Kontakt zu diesem Objekt schon ein Gespräch geführt bzw. läuft ein Auftrag?"""
        rows = self.q(
            "SELECT status, outcome FROM jobs WHERE client_id=? AND IFNULL(property_id,0)=IFNULL(?,0)",
            (client_id, property_id),
        )
        for r in rows:
            if r["status"] in ("pending", "calling"):
                return True
            if r["outcome"] and r["outcome"] not in ("nicht_erreicht", "nummer_ungueltig", "technischer_fehler"):
                return True
        return False

    def add_job(self, *, activity_id, client_id, property_id, broker_id, phone, status, next_attempt_at=None, note=None) -> int:
        ts = now_iso()
        return self.x(
            """INSERT INTO jobs(activity_id,client_id,property_id,broker_id,phone,status,next_attempt_at,note,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (activity_id, client_id, property_id, broker_id, phone, status, next_attempt_at, note, ts, ts),
        )

    def update_job(self, job_id: int, **fields) -> None:
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k}=?" for k in fields)
        self.x(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))

    def get_job(self, job_id: int):
        r = self.q("SELECT * FROM jobs WHERE id=?", (job_id,))
        return r[0] if r else None

    def job_by_call(self, call_id: str):
        r = self.q("SELECT * FROM jobs WHERE last_call_id=?", (call_id,))
        return r[0] if r else None

    def due_jobs(self, now: str, limit: int):
        return self.q(
            "SELECT * FROM jobs WHERE status='pending' AND next_attempt_at<=? ORDER BY next_attempt_at LIMIT ?",
            (now, limit),
        )

    def calling_jobs(self):
        return self.q("SELECT * FROM jobs WHERE status='calling'")

    def recent_jobs(self, limit: int = 50):
        return self.q("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))

    # --- Sperrliste ----------------------------------------------------------
    def is_blocked(self, phone: str | None) -> bool:
        return bool(phone) and bool(self.q("SELECT 1 FROM blocklist WHERE phone=?", (phone,)))

    def block(self, phone: str, client_id: int | None) -> None:
        self.x("INSERT OR IGNORE INTO blocklist(phone,client_id,created_at) VALUES(?,?,?)", (phone, client_id, now_iso()))

    # --- Webhook-Deduplizierung ---------------------------------------------
    def mark_processed(self, key: str) -> bool:
        """True, wenn der Schlüssel neu war (also jetzt verarbeitet werden soll)."""
        with self.lock:
            try:
                self.conn.execute("INSERT INTO processed(key,created_at) VALUES(?,?)", (key, now_iso()))
                return True
            except sqlite3.IntegrityError:
                return False

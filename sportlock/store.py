"""SQLite persistence: lock outcomes, overrides, sessions and small bits of saved state."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path

DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "sportlock"
DB_PATH = DATA_DIR / "sportlock.db"
BACKUPS_KEPT = 7

SCHEMA = """
CREATE TABLE IF NOT EXISTS lock_events (
    key TEXT PRIMARY KEY,          -- window start, e.g. 2026-10-05T18:00, or test-<timestamp>
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    began_at TEXT NOT NULL,        -- when the screen actually locked
    ended_at TEXT,
    outcome TEXT                   -- completed | override | expired
);
CREATE TABLE IF NOT EXISTS overrides (
    id INTEGER PRIMARY KEY,
    lock_key TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    unlock_at TEXT NOT NULL,
    cancelled_at TEXT,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT NOT NULL,
    kind TEXT NOT NULL,            -- placeholder | generated | local | outside
    lock_key TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # -- saved state ---------------------------------------------------------------------------

    def get(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def put(self, key: str, value) -> None:
        self.db.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, json.dumps(value)))

    def delete(self, key: str) -> None:
        self.db.execute("DELETE FROM kv WHERE key = ?", (key,))

    # -- locks ---------------------------------------------------------------------------------

    def lock_began(self, key: str, start: datetime, end: datetime, now: datetime) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO lock_events (key, start, end, began_at) VALUES (?, ?, ?, ?)",
            (key, _iso(start), _iso(end), _iso(now)),
        )

    def lock_ended(self, key: str, outcome: str, now: datetime) -> None:
        self.db.execute(
            "UPDATE lock_events SET ended_at = ?, outcome = ? WHERE key = ? AND ended_at IS NULL",
            (_iso(now), outcome, key),
        )

    def ended_early(self) -> set[str]:
        """Window keys ended by a finished session or an override."""
        rows = self.db.execute("SELECT key FROM lock_events WHERE outcome IN ('completed', 'override')")
        return {row["key"] for row in rows}

    def recent_locks(self, limit: int = 20) -> list[dict]:
        rows = self.db.execute("SELECT * FROM lock_events ORDER BY began_at DESC LIMIT ?", (limit,))
        return [dict(row) for row in rows]

    # -- overrides -----------------------------------------------------------------------------

    def start_override(self, lock_key: str, now: datetime, unlock_at: datetime) -> None:
        self.db.execute(
            "INSERT INTO overrides (lock_key, requested_at, unlock_at) VALUES (?, ?, ?)",
            (lock_key, _iso(now), _iso(unlock_at)),
        )

    def pending_override(self, lock_key: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM overrides WHERE lock_key = ? AND cancelled_at IS NULL AND completed_at IS NULL"
            " ORDER BY id DESC LIMIT 1",
            (lock_key,),
        ).fetchone()
        return dict(row) if row else None

    def finish_override(self, override_id: int, now: datetime, *, cancelled: bool) -> None:
        column = "cancelled_at" if cancelled else "completed_at"
        self.db.execute(f"UPDATE overrides SET {column} = ? WHERE id = ?", (_iso(now), override_id))

    # -- sessions ------------------------------------------------------------------------------

    def add_session(self, *, day: date, started_at: datetime | None, finished_at: datetime, kind: str,
                    lock_key: str | None = None, notes: str = "") -> None:
        self.db.execute(
            "INSERT INTO sessions (day, started_at, finished_at, kind, lock_key, notes) VALUES (?, ?, ?, ?, ?, ?)",
            (day.isoformat(), started_at and _iso(started_at), _iso(finished_at), kind, lock_key, notes),
        )

    def trained_days(self) -> set[date]:
        """Days with a finished session that counts towards locks (outside sessions never do)."""
        rows = self.db.execute("SELECT DISTINCT day FROM sessions WHERE kind != 'outside'")
        return {date.fromisoformat(row["day"]) for row in rows}

    # -- backups -------------------------------------------------------------------------------

    def backup_daily(self, today: date) -> None:
        if self.get("last_backup") == today.isoformat():
            return
        backups = self.path.parent / "backups"
        backups.mkdir(exist_ok=True)
        with sqlite3.connect(backups / f"sportlock-{today.isoformat()}.db") as target:
            self.db.backup(target)
        for old in sorted(backups.glob("sportlock-*.db"))[:-BACKUPS_KEPT]:
            old.unlink()
        self.put("last_backup", today.isoformat())

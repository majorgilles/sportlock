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

# Each entry upgrades the schema by one version (PRAGMA user_version).
MIGRATIONS = [
    """
    ALTER TABLE sessions ADD COLUMN status TEXT NOT NULL DEFAULT 'finished';  -- in_progress | finished | abandoned | overridden
    ALTER TABLE sessions ADD COLUMN title TEXT;
    ALTER TABLE sessions ADD COLUMN day_type TEXT;       -- hard | light | mobility
    ALTER TABLE sessions ADD COLUMN plan_source TEXT;    -- starter | generated | local
    ALTER TABLE sessions ADD COLUMN rpe INTEGER;
    ALTER TABLE sessions ADD COLUMN calories INTEGER;
    ALTER TABLE sessions ADD COLUMN avg_hr INTEGER;
    ALTER TABLE sessions ADD COLUMN body_weight REAL;
    CREATE TABLE session_exercises (
        id INTEGER PRIMARY KEY,
        session_id INTEGER NOT NULL REFERENCES sessions(id),
        exercise TEXT NOT NULL,            -- catalog id
        name TEXT NOT NULL,
        pattern TEXT NOT NULL,             -- push | pull | squat | hinge | core | mobility | warmup
        kind TEXT NOT NULL,                -- reps | hold | timed
        target TEXT NOT NULL,              -- JSON: sets, reps [min, max] or seconds, rest
        status TEXT NOT NULL DEFAULT 'pending',  -- pending | done | skipped | swapped
        rpe INTEGER,
        note TEXT,
        skip_reason TEXT,
        swapped_to INTEGER REFERENCES session_exercises(id),
        started_at TEXT,
        ended_at TEXT
    );
    CREATE TABLE sets (
        id INTEGER PRIMARY KEY,
        session_exercise_id INTEGER NOT NULL REFERENCES session_exercises(id),
        set_no INTEGER NOT NULL,
        reps INTEGER,
        seconds REAL NOT NULL,             -- measured from Start to Stop
        load_kg REAL,
        rest_seconds REAL,                 -- since the previous set of this exercise ended
        started_at TEXT NOT NULL,
        ended_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE ladders (
        chain TEXT PRIMARY KEY,            -- progression chain, e.g. push-horizontal
        exercise TEXT NOT NULL,            -- current exercise on the chain
        target TEXT NOT NULL,              -- JSON target for next time
        reason TEXT,                       -- why it is here (last applied proposal)
        updated_at TEXT NOT NULL
    );
    CREATE TABLE proposals (
        id INTEGER PRIMARY KEY,
        session_id INTEGER NOT NULL REFERENCES sessions(id),
        session_exercise_id INTEGER REFERENCES session_exercises(id),
        chain TEXT NOT NULL,
        rule TEXT NOT NULL,                -- up | add | hold | down | too-hard
        from_exercise TEXT NOT NULL,
        from_target TEXT NOT NULL,
        to_exercise TEXT NOT NULL,
        to_target TEXT NOT NULL,
        reason TEXT NOT NULL,
        status TEXT NOT NULL,              -- applied | overridden
        decided_by TEXT NOT NULL,          -- rules | agent
        override_reason TEXT,
        created_at TEXT NOT NULL
    );
    """,
]


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
            self.db.executescript(f"BEGIN; {script}; PRAGMA user_version = {number}; COMMIT;")

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
        rows = self.db.execute("SELECT key FROM lock_events WHERE outcome IN ('completed', 'override', 'rest')")
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

    def trained_days(self) -> set[date]:
        """Days with a finished session that counts towards locks (outside and test sessions never do)."""
        rows = self.db.execute(
            "SELECT DISTINCT day FROM sessions WHERE status = 'finished' AND kind NOT IN ('outside', 'test', 'placeholder')"
        )
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

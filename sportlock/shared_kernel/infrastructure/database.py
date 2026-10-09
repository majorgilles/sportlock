"""The SQLite database: connection, schema migrations, a small key-value table, daily backups."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable
from datetime import date
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
    outcome TEXT                   -- completed | override | expired | rest
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
    kind TEXT NOT NULL,            -- scheduled | manual | test | outside | placeholder
    lock_key TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _move_coach_runs(db: sqlite3.Connection) -> None:
    row = db.execute("SELECT value FROM kv WHERE key = 'agent_runs'").fetchone()
    for run in json.loads(row[0]) if row else []:
        db.execute("INSERT INTO coach_runs (at, seconds, ok, error) VALUES (?, ?, ?, ?)",
                   (run["at"], run["seconds"], int(run["ok"]), run.get("error")))
    db.execute("DELETE FROM kv WHERE key IN ('agent_runs', 'coach_memory', 'coach_memory_previous')")


# Each entry upgrades the schema by one version (PRAGMA user_version): SQL, or a function for
# data moves.
MIGRATIONS: list[str | Callable[[sqlite3.Connection], None]] = [
    """
    ALTER TABLE sessions ADD COLUMN status TEXT NOT NULL DEFAULT 'finished';  -- in_progress | finished | abandoned | overridden
    ALTER TABLE sessions ADD COLUMN title TEXT;
    ALTER TABLE sessions ADD COLUMN day_type TEXT;       -- hard | light | mobility
    ALTER TABLE sessions ADD COLUMN plan_source TEXT;    -- generated | local
    ALTER TABLE sessions ADD COLUMN rpe INTEGER;
    ALTER TABLE sessions ADD COLUMN calories INTEGER;
    ALTER TABLE sessions ADD COLUMN avg_hr INTEGER;
    ALTER TABLE sessions ADD COLUMN body_weight REAL;
    CREATE TABLE session_exercises (
        id INTEGER PRIMARY KEY,
        session_id INTEGER NOT NULL REFERENCES sessions(id),
        exercise TEXT NOT NULL,            -- catalogue id
        name TEXT NOT NULL,
        pattern TEXT NOT NULL,
        kind TEXT NOT NULL,                -- reps | hold | timed
        target TEXT NOT NULL,              -- JSON: sets, reps [min, max] or seconds, rest, sides
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
        seconds REAL NOT NULL,             -- measured from Start to Stop (per side for holds on each side)
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
        rule TEXT NOT NULL,                -- up | add | hold | down | too-hard | override
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
    """
    CREATE TABLE coach_runs (
        id INTEGER PRIMARY KEY,
        at TEXT NOT NULL,
        seconds INTEGER NOT NULL,
        ok INTEGER NOT NULL,
        error TEXT
    );
    CREATE TABLE coach_memory_notes (      -- one row per version of a note; see the ADR on coach memory
        id INTEGER PRIMARY KEY,
        note_id INTEGER NOT NULL,          -- stable identity across versions
        topic TEXT NOT NULL,
        text TEXT NOT NULL,
        valid_from TEXT NOT NULL,          -- day this version was written
        valid_to TEXT,                     -- NULL while current
        written_by TEXT NOT NULL,          -- coach-run:<id>
        ended_by TEXT,                     -- coach-run:<id> | user
        end_reason TEXT                    -- updated | deleted | forgotten
    );
    CREATE INDEX coach_memory_current ON coach_memory_notes (valid_to);
    """,
    _move_coach_runs,
]


class Database:
    """One connection, shared by the repositories (autocommit; SQLite serialises writes)."""

    def __init__(self, path: Path = DB_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        for number, step in enumerate(MIGRATIONS[version:], start=version + 1):
            if callable(step):
                self.db.execute("BEGIN")
                step(self.db)
                self.db.execute(f"PRAGMA user_version = {number}")
                self.db.execute("COMMIT")
            else:
                self.db.executescript(f"BEGIN; {step}; PRAGMA user_version = {number}; COMMIT;")

    def execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        """Run one statement."""
        return self.db.execute(sql, params)

    def get(self, key: str, default=None):
        """A JSON value from the key-value table."""
        row = self.db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def put(self, key: str, value) -> None:
        """Store a JSON value in the key-value table."""
        self.db.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, json.dumps(value)))

    def delete(self, key: str) -> None:
        """Remove a key."""
        self.db.execute("DELETE FROM kv WHERE key = ?", (key,))

    def backup_daily(self, today: date) -> None:
        """One backup a day, the last BACKUPS_KEPT kept."""
        if self.get("last_backup") == today.isoformat():
            return
        backups = self.path.parent / "backups"
        backups.mkdir(exist_ok=True)
        with sqlite3.connect(backups / f"sportlock-{today.isoformat()}.db") as target:
            self.db.backup(target)
        for old in sorted(backups.glob("sportlock-*.db"))[:-BACKUPS_KEPT]:
            old.unlink()
        self.put("last_backup", today.isoformat())

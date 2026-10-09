"""SQLite adapters for locks."""

from __future__ import annotations

from datetime import date, datetime
from typing import override

from sportlock.locks.domain.locks import ActiveLock, LockOutcome, LockPlan, PendingOverride
from sportlock.locks.domain.repositories import (
    LockEventRepositoryProtocol,
    LockStateRepositoryProtocol,
    OverrideRepositoryProtocol,
)
from sportlock.locks.domain.schedule import Window
from sportlock.shared_kernel.infrastructure.database import Database
from sportlock.shared_kernel.time import iso

PLANS_KEPT, WARNINGS_KEPT = 40, 50


class SqliteLockEventRepository(LockEventRepositoryProtocol):
    """lock_events table."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @override
    def began(self, key: str, start: datetime, end: datetime, now: datetime) -> None:
        self.database.execute(
            "INSERT OR IGNORE INTO lock_events (key, start, end, began_at) VALUES (?, ?, ?, ?)",
            (key, iso(start), iso(end), iso(now)),
        )

    @override
    def ended(self, key: str, outcome: LockOutcome, now: datetime) -> None:
        self.database.execute(
            "UPDATE lock_events SET ended_at = ?, outcome = ? WHERE key = ? AND ended_at IS NULL",
            (iso(now), outcome, key),
        )

    @override
    def outcome(self, key: str) -> str | None:
        row = self.database.execute("SELECT outcome FROM lock_events WHERE key = ?", (key,)).fetchone()
        return row["outcome"] if row else None

    @override
    def ended_early(self) -> set[str]:
        rows = self.database.execute("SELECT key FROM lock_events WHERE outcome IN ('completed', 'override', 'rest')")
        return {row["key"] for row in rows}

    @override
    def rest_days(self) -> set[date]:
        rows = self.database.execute("SELECT start FROM lock_events WHERE outcome = 'rest'")
        return {date.fromisoformat(row["start"][:10]) for row in rows}

    @override
    def recent(self, limit: int) -> list[dict]:
        rows = self.database.execute("SELECT * FROM lock_events ORDER BY began_at DESC LIMIT ?", (limit,))
        return [dict(row) for row in rows]

    @override
    def between(self, first: date, last: date) -> list[dict]:
        rows = self.database.execute(
            "SELECT * FROM lock_events WHERE substr(start, 1, 10) BETWEEN ? AND ?",
            (first.isoformat(), last.isoformat()),
        )
        return [dict(row) for row in rows]


class SqliteOverrideRepository(OverrideRepositoryProtocol):
    """overrides table."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @override
    def start(self, lock_key: str, now: datetime, unlock_at: datetime) -> None:
        self.database.execute(
            "INSERT INTO overrides (lock_key, requested_at, unlock_at) VALUES (?, ?, ?)",
            (lock_key, iso(now), iso(unlock_at)),
        )

    @override
    def pending(self, lock_key: str) -> PendingOverride | None:
        row = self.database.execute(
            "SELECT * FROM overrides WHERE lock_key = ? AND cancelled_at IS NULL AND completed_at IS NULL"
            " ORDER BY id DESC LIMIT 1",
            (lock_key,),
        ).fetchone()
        return (
            PendingOverride(id=row["id"], lock_key=row["lock_key"], unlock_at=datetime.fromisoformat(row["unlock_at"]))
            if row
            else None
        )

    @override
    def finish(self, override_id: int, now: datetime, *, cancelled: bool) -> None:
        column = "cancelled_at" if cancelled else "completed_at"
        self.database.execute(f"UPDATE overrides SET {column} = ? WHERE id = ?", (iso(now), override_id))


class KvLockStateRepository(LockStateRepositoryProtocol):
    """Lock state kept in the key-value table (lock_plans, manual_lock, warned, restore)."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @override
    def plans(self) -> dict[str, LockPlan]:
        return {key: LockPlan.from_dict(value) for key, value in self.database.get("lock_plans", {}).items()}

    @override
    def save_plan(self, key: str, plan: LockPlan) -> None:
        plans = self.database.get("lock_plans", {})
        plans[key] = plan.to_dict()
        self.database.put("lock_plans", dict(list(plans.items())[-PLANS_KEPT:]))

    @override
    def manual_lock(self) -> ActiveLock | None:
        saved = self.database.get("manual_lock")
        if not saved:
            return None
        window = Window(start=datetime.fromisoformat(saved["start"]), end=datetime.fromisoformat(saved["end"]))
        return ActiveLock(key=saved["key"], window=window, kind="manual")

    @override
    def set_manual_lock(self, lock: ActiveLock | None) -> None:
        if lock is None:
            self.database.delete("manual_lock")
        else:
            self.database.put(
                "manual_lock",
                {"key": lock.key, "start": lock.window.start.isoformat(), "end": lock.window.end.isoformat()},
            )

    @override
    def warned(self) -> list[str]:
        return self.database.get("warned", [])

    @override
    def add_warned(self, tag: str) -> None:
        self.database.put("warned", (self.warned() + [tag])[-WARNINGS_KEPT:])

    @override
    def desktop_to_restore(self) -> dict | None:
        return self.database.get("restore")

    @override
    def set_desktop_to_restore(self, saved: dict | None) -> None:
        if saved is None:
            self.database.delete("restore")
        else:
            self.database.put("restore", saved)

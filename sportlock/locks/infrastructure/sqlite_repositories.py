"""SQLite adapters for locks: the lock_events and overrides tables, and lock state in the key-value table."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import override

from sportlock.locks.domain.locks import ActiveLock, DesktopToRestore, GivenWarning, LockEvent, LockPlan, Override
from sportlock.locks.domain.repositories import (
    DesktopToRestoreRepositoryProtocol,
    GivenWarningFilters,
    GivenWarningRepositoryProtocol,
    LockEventFilters,
    LockEventRepositoryProtocol,
    LockPlanFilters,
    LockPlanRepositoryProtocol,
    ManualLockRepositoryProtocol,
    OverrideFilters,
    OverrideRepositoryProtocol,
)
from sportlock.locks.domain.schedule import Window
from sportlock.shared_kernel.infrastructure.database import Database
from sportlock.shared_kernel.time import iso

PLANS_KEPT, WARNINGS_KEPT = 40, 50
PLANS_KEY, MANUAL_LOCK_KEY, WARNED_KEY, RESTORE_KEY = "lock_plans", "manual_lock", "warned", "restore"
NO_LIMIT = -1


def _optional_time(value: datetime | None) -> str | None:
    return iso(value) if value else None


def _parsed_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class SqliteLockEventRepository(LockEventRepositoryProtocol):
    """lock_events table."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @override
    def create(self, entity: LockEvent) -> LockEvent:
        self.database.execute(
            "INSERT OR IGNORE INTO lock_events (key, start, end, began_at) VALUES (?, ?, ?, ?)",
            (entity.key, entity.start, entity.end, entity.began_at),
        )
        return entity

    @override
    def get_by_id(self, identifier: str) -> LockEvent | None:
        row = self.database.execute("SELECT * FROM lock_events WHERE key = ?", (identifier,)).fetchone()
        return LockEvent(**dict(row)) if row else None

    @override
    def find(self, filters: LockEventFilters) -> list[LockEvent]:
        conditions, parameters = [], []
        if filters.outcomes is not None:
            conditions.append(f"outcome IN ({', '.join('?' for _ in filters.outcomes)})")
            parameters += list(filters.outcomes)
        if filters.first_day is not None:
            conditions.append("substr(start, 1, 10) >= ?")
            parameters.append(filters.first_day.isoformat())
        if filters.last_day is not None:
            conditions.append("substr(start, 1, 10) <= ?")
            parameters.append(filters.last_day.isoformat())
        where = " AND ".join(conditions) or "1"
        order = "began_at DESC" if filters.newest_first else "rowid"
        limit = NO_LIMIT if filters.latest is None else filters.latest
        rows = self.database.execute(
            f"SELECT * FROM lock_events WHERE {where} ORDER BY {order} LIMIT ?", (*parameters, limit)
        )
        return [LockEvent(**dict(row)) for row in rows]

    @override
    def update(self, entity: LockEvent) -> LockEvent:
        self.database.execute(
            "UPDATE lock_events SET ended_at = ?, outcome = ? WHERE key = ?",
            (entity.ended_at, entity.outcome, entity.key),
        )
        return entity


def _override(row: sqlite3.Row) -> Override:
    return Override(
        id=row["id"],
        lock_key=row["lock_key"],
        requested_at=datetime.fromisoformat(row["requested_at"]),
        unlock_at=datetime.fromisoformat(row["unlock_at"]),
        cancelled_at=_parsed_time(row["cancelled_at"]),
        completed_at=_parsed_time(row["completed_at"]),
    )


class SqliteOverrideRepository(OverrideRepositoryProtocol):
    """overrides table."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @override
    def create(self, entity: Override) -> Override:
        identifier = self.database.execute(
            "INSERT INTO overrides (lock_key, requested_at, unlock_at) VALUES (?, ?, ?)",
            (entity.lock_key, iso(entity.requested_at), iso(entity.unlock_at)),
        ).lastrowid
        return entity.model_copy(update={"id": identifier})

    @override
    def find(self, filters: OverrideFilters) -> list[Override]:
        conditions, parameters = [], []
        if filters.lock_key is not None:
            conditions.append("lock_key = ?")
            parameters.append(filters.lock_key)
        if filters.pending:
            conditions.append("cancelled_at IS NULL AND completed_at IS NULL")
        where = " AND ".join(conditions) or "1"
        limit = NO_LIMIT if filters.latest is None else filters.latest
        rows = self.database.execute(
            f"SELECT * FROM overrides WHERE {where} ORDER BY id DESC LIMIT ?", (*parameters, limit)
        )
        return [_override(row) for row in rows]

    @override
    def update(self, entity: Override) -> Override:
        self.database.execute(
            "UPDATE overrides SET cancelled_at = ?, completed_at = ? WHERE id = ?",
            (_optional_time(entity.cancelled_at), _optional_time(entity.completed_at), entity.id),
        )
        return entity


class KvLockPlanRepository(LockPlanRepositoryProtocol):
    """Lock plans kept in the key-value table under "lock_plans", by window key."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def _stored(self) -> dict:
        return self.database.get(PLANS_KEY, {})

    @override
    def create(self, entity: LockPlan) -> LockPlan:
        plans = self._stored()
        plans[entity.key] = entity.to_dict()
        self.database.put(PLANS_KEY, dict(list(plans.items())[-PLANS_KEPT:]))
        return entity

    @override
    def get_by_id(self, identifier: str) -> LockPlan | None:
        stored = self._stored().get(identifier)
        return LockPlan.from_dict(identifier, stored) if stored else None

    @override
    def find(self, filters: LockPlanFilters) -> list[LockPlan]:
        return [LockPlan.from_dict(key, value) for key, value in self._stored().items()]


class KvManualLockRepository(ManualLockRepositoryProtocol):
    """The manual lock kept in the key-value table under "manual_lock"."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @override
    def get(self) -> ActiveLock | None:
        saved = self.database.get(MANUAL_LOCK_KEY)
        if not saved:
            return None
        window = Window(start=datetime.fromisoformat(saved["start"]), end=datetime.fromisoformat(saved["end"]))
        return ActiveLock(key=saved["key"], window=window, kind="manual")

    @override
    def update(self, entity: ActiveLock | None) -> ActiveLock | None:
        if entity is None:
            self.database.delete(MANUAL_LOCK_KEY)
        else:
            self.database.put(
                MANUAL_LOCK_KEY,
                {"key": entity.key, "start": entity.window.start.isoformat(), "end": entity.window.end.isoformat()},
            )
        return entity


class KvGivenWarningRepository(GivenWarningRepositoryProtocol):
    """Given warnings kept in the key-value table under "warned", as "<window key>:<minutes>" tags."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def _tags(self) -> list[str]:
        return self.database.get(WARNED_KEY, [])

    @override
    def create(self, entity: GivenWarning) -> GivenWarning:
        self.database.put(WARNED_KEY, [*self._tags(), entity.tag][-WARNINGS_KEPT:])
        return entity

    @override
    def find(self, filters: GivenWarningFilters) -> list[GivenWarning]:
        warnings = [GivenWarning.from_tag(tag) for tag in self._tags()]
        if filters.window_key is None:
            return warnings
        return [warning for warning in warnings if warning.window_key == filters.window_key]


class KvDesktopToRestoreRepository(DesktopToRestoreRepositoryProtocol):
    """The desktop state to restore, kept in the key-value table under "restore"."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @override
    def get(self) -> DesktopToRestore | None:
        saved = self.database.get(RESTORE_KEY)
        return DesktopToRestore.from_dict(saved) if saved is not None else None

    @override
    def update(self, entity: DesktopToRestore | None) -> DesktopToRestore | None:
        if entity is None:
            self.database.delete(RESTORE_KEY)
        else:
            self.database.put(RESTORE_KEY, entity.to_dict())
        return entity

"""Persistence ports for locks."""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

from sportlock.locks.domain.locks import ActiveLock, LockOutcome, LockPlan, PendingOverride


class LockEventRepositoryProtocol(Protocol):
    """When each lock began and how it ended."""

    def began(self, key: str, start: datetime, end: datetime, now: datetime) -> None:
        """Record a lock starting (no-op if already recorded)."""
        ...

    def ended(self, key: str, outcome: LockOutcome, now: datetime) -> None:
        """Record how a lock ended (no-op if it already ended)."""
        ...

    def outcome(self, key: str) -> str | None:
        """How the lock ended so far, or None."""
        ...

    def ended_early(self) -> set[str]:
        """Keys of locks ended by a finished session, an override or a rest day."""
        ...

    def rest_days(self) -> set[date]:
        """Days whose scheduled lock became a rest day."""
        ...

    def recent(self, limit: int) -> list[dict]:
        """The latest locks, newest first, as {key, start, end, began_at, ended_at, outcome}."""
        ...

    def between(self, first: date, last: date) -> list[dict]:
        """Locks starting between the two days (inclusive)."""
        ...


class OverrideRepositoryProtocol(Protocol):
    """Override countdowns."""

    def start(self, lock_key: str, now: datetime, unlock_at: datetime) -> None:
        """Start a countdown."""
        ...

    def pending(self, lock_key: str) -> PendingOverride | None:
        """The running countdown for this lock, if any."""
        ...

    def finish(self, override_id: int, now: datetime, *, cancelled: bool) -> None:
        """End a countdown: completed (the lock ends) or cancelled."""
        ...


class LockStateRepositoryProtocol(Protocol):
    """Small persisted lock state that must survive a restart."""

    def plans(self) -> dict[str, LockPlan]:
        """Plans decided for scheduled locks, by window key."""
        ...

    def save_plan(self, key: str, plan: LockPlan) -> None:
        """Store a decided plan (the latest 40 are kept)."""
        ...

    def manual_lock(self) -> ActiveLock | None:
        """The lock started with `sportlock start`, if any."""
        ...

    def set_manual_lock(self, lock: ActiveLock | None) -> None:
        """Store or clear the manual lock."""
        ...

    def warned(self) -> list[str]:
        """Warnings already given, as "<window key>:<minutes>"."""
        ...

    def add_warned(self, tag: str) -> None:
        """Remember a warning (the latest 50 are kept)."""
        ...

    def desktop_to_restore(self) -> dict | None:
        """Media and idle state saved when the lock began, to restore when it ends."""
        ...

    def set_desktop_to_restore(self, saved: dict | None) -> None:
        """Save or clear the state to restore."""
        ...


class LockRuntimeProtocol(Protocol):
    """In-memory state of the running service (lost on restart, by design)."""

    current: ActiveLock | None  # lock the screen is under
    test_lock: ActiveLock | None
    waiting_for_omarchy: bool

"""Locks as they happen: the one on screen, the plan decided for a scheduled one, overrides."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from sportlock.locks.domain.schedule import Window
from sportlock.shared_kernel.base import ValueObject
from sportlock.shared_kernel.time import iso

type LockKind = Literal["scheduled", "manual", "test"]
type LockMode = Literal["hard", "recovery", "rest"]
type LockOutcome = Literal["completed", "override", "expired", "rest"]


class ActiveLock(ValueObject):
    """A lock the screen is (or should be) under."""

    key: str
    window: Window
    kind: LockKind
    mode: LockMode | None = None  # hard | recovery, as planned for a scheduled lock

    @property
    def overridable(self) -> bool:
        """Test locks can't be overridden: they only last a minute."""
        return self.kind != "test"

    @property
    def test(self) -> bool:
        """Test locks never count as training."""
        return self.kind == "test"


class LockPlan(ValueObject):
    """What a scheduled lock was decided to be, about 10 minutes before it starts. `key` is the window's key."""

    key: str
    mode: LockMode
    minutes: int  # lock length (shortened for recovery; 0 for rest)
    reason: str
    end: datetime

    @classmethod
    def from_dict(cls, key: str, data: dict) -> LockPlan:
        """Read the stored JSON shape, kept under the window's key."""
        return cls(
            key=key,
            mode=data["mode"],
            minutes=data["minutes"],
            reason=data.get("reason", ""),
            end=datetime.fromisoformat(data["end"]),
        )

    def to_dict(self) -> dict:
        """The stored JSON shape (without the key, which the store keeps it under)."""
        return {"mode": self.mode, "minutes": self.minutes, "reason": self.reason, "end": self.end.isoformat()}


class Override(ValueObject):
    """An override countdown: requested, then completed (the lock ends) or cancelled. `id` is set once stored."""

    lock_key: str
    requested_at: datetime
    unlock_at: datetime
    cancelled_at: datetime | None = None
    completed_at: datetime | None = None
    id: int | None = None

    @property
    def pending(self) -> bool:
        """Whether the countdown is still running."""
        return self.cancelled_at is None and self.completed_at is None

    def finished(self, now: datetime, *, cancelled: bool) -> Override:
        """The countdown ended: cancelled by the athlete, or completed so the lock ends."""
        return self.model_copy(update={"cancelled_at" if cancelled else "completed_at": now})


class LockEvent(ValueObject):
    """When a lock began and how it ended, with times as stored (seconds-precision ISO text)."""

    key: str
    start: str
    end: str
    began_at: str
    ended_at: str | None = None
    outcome: LockOutcome | None = None

    @classmethod
    def began(cls, key: str, start: datetime, end: datetime, now: datetime) -> LockEvent:
        """A lock that just began."""
        return cls(key=key, start=iso(start), end=iso(end), began_at=iso(now))

    def ended(self, outcome: LockOutcome, now: datetime) -> LockEvent:
        """The lock ended this way; a lock that already ended keeps its first ending."""
        if self.ended_at is not None:
            return self
        return self.model_copy(update={"ended_at": iso(now), "outcome": outcome})

    def to_dict(self) -> dict:
        """The shape the socket API and the calendar read: {key, start, end, began_at, ended_at, outcome}."""
        return self.model_dump()


EARLY_OUTCOMES: tuple[LockOutcome, ...] = ("completed", "override", "rest")


def ended_early(events: list[LockEvent]) -> set[str]:
    """Keys of locks ended by a finished session, an override or a rest day."""
    return {event.key for event in events if event.outcome in EARLY_OUTCOMES}


def rest_days(events: list[LockEvent]) -> set[date]:
    """Days whose scheduled lock became a rest day."""
    return {date.fromisoformat(event.start[:10]) for event in events if event.outcome == "rest"}


class GivenWarning(ValueObject):
    """A warning already given before a lock: the window's key and the minutes it said were left."""

    window_key: str
    minutes: int

    @classmethod
    def from_tag(cls, tag: str) -> GivenWarning:
        """Read the stored "<window key>:<minutes>" form (window keys contain colons themselves)."""
        window_key, minutes = tag.rsplit(":", 1)
        return cls(window_key=window_key, minutes=int(minutes))

    @property
    def tag(self) -> str:
        """The stored "<window key>:<minutes>" form."""
        return f"{self.window_key}:{self.minutes}"


class DesktopToRestore(ValueObject):
    """Media and idle state saved when a lock began, to put back when it ends (also after a crash)."""

    paused: list[str] = []
    stay_awake: bool | None = None
    music_volume: float | None = None

    @classmethod
    def from_dict(cls, data: dict) -> DesktopToRestore:
        """Read the stored JSON shape."""
        return cls(
            paused=data.get("paused") or [], stay_awake=data.get("stay_awake"), music_volume=data.get("music_volume")
        )

    def to_dict(self) -> dict:
        """The stored JSON shape."""
        return {"paused": self.paused, "stay_awake": self.stay_awake, "music_volume": self.music_volume}

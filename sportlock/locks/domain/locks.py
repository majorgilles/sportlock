"""Locks as they happen: the one on screen, the plan decided for a scheduled one, overrides."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from sportlock.locks.domain.schedule import Window
from sportlock.shared_kernel.base import ValueObject

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
    """What a scheduled lock was decided to be, about 10 minutes before it starts."""

    mode: LockMode
    minutes: int  # lock length (shortened for recovery; 0 for rest)
    reason: str
    end: datetime

    @classmethod
    def from_dict(cls, data: dict) -> LockPlan:
        """Read the stored JSON shape."""
        return cls(mode=data["mode"], minutes=data["minutes"], reason=data.get("reason", ""),
                   end=datetime.fromisoformat(data["end"]))

    def to_dict(self) -> dict:
        """The stored JSON shape."""
        return {"mode": self.mode, "minutes": self.minutes, "reason": self.reason, "end": self.end.isoformat()}


class PendingOverride(ValueObject):
    """An override waiting out its countdown before it unlocks."""

    id: int
    lock_key: str
    unlock_at: datetime

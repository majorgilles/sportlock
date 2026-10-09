"""The target of one exercise: how many sets, how many reps or seconds, how long to rest.

Shared by training (what to do now), progression (what to do next time) and coaching (what the
coach plans). Stored as JSON in the same shape since the first schema, so `to_dict` and
`from_dict` must stay compatible with existing rows.
"""

from __future__ import annotations

from typing import Literal

from sportlock.shared_kernel.base import ValueObject

type Kind = Literal["reps", "hold", "timed"]
type Sides = Literal["each", "alternating"]

SECONDS_PER_REP = 3
DEFAULT_SECONDS: dict[str, int] = {"hold": 30, "timed": 120}


class Target(ValueObject):
    """Sets × (reps range or seconds), rest between sets; reps and seconds count per side."""

    sets: int
    rest: int = 0
    reps: tuple[int, int] | None = None
    seconds: float | None = None
    sides: Sides | None = None
    progress: str | None = None  # why the exercise is at this level, shown on its card

    @classmethod
    def from_dict(cls, data: dict) -> Target:
        """Read the stored JSON shape (unknown keys are ignored)."""
        return cls(sets=data["sets"], rest=data.get("rest", 0), reps=data.get("reps"), seconds=data.get("seconds"),
                   sides=data.get("sides") or None, progress=data.get("progress") or None)

    def to_dict(self) -> dict:
        """The stored JSON shape."""
        data: dict = {"sets": self.sets, "rest": self.rest}
        if self.reps is not None:
            data["reps"] = list(self.reps)
        if self.seconds is not None:
            data["seconds"] = self.seconds
        if self.sides:
            data["sides"] = self.sides
        if self.progress:
            data["progress"] = self.progress
        return data

    def with_(self, **changes: object) -> Target:
        """A copy with some fields changed (validated)."""
        return Target.model_validate({**self.model_dump(), **changes})

    def work_seconds(self) -> float:
        """Naive length of one set's work, both sides included."""
        work = self.reps[1] * SECONDS_PER_REP if self.reps else (self.seconds or 0)
        return work * 2 if self.sides else work

    def naive_seconds(self, sets: int | None = None) -> float:
        """Naive length of `sets` sets (default: all) including the rests."""
        return (sets or self.sets) * (self.work_seconds() + self.rest)

    def for_kind(self, kind: Kind) -> Target:
        """The same target expressed for another kind of exercise (reps ↔ hold/timed)."""
        if kind == "reps":
            return self.with_(reps=self.reps or (8, 12), seconds=None)
        return self.with_(reps=None, seconds=self.seconds or DEFAULT_SECONDS[kind])

    def problem(self, kind: str) -> str | None:
        """Why this target can't be used for an exercise of `kind`, or None."""
        if self.sets < 1:
            return f"sets is {self.sets!r}"
        if kind == "reps":
            if not self.reps or self.reps[0] > self.reps[1]:
                return f"a reps exercise without a valid rep range ({self.to_dict()})"
        elif not self.seconds or self.seconds <= 0:
            return f"a {kind} exercise without a duration ({self.to_dict()})"
        return None

"""A session plan: the ordered exercises of one session with their targets.

Produced by the local planner (progression) and by the coach (coaching), consumed by training.
"""

from __future__ import annotations

from typing import Literal

from sportlock.shared_kernel.base import ValueObject
from sportlock.shared_kernel.targets import Target

type DayType = Literal["hard", "light", "mobility"]


class PlannedExercise(ValueObject):
    """One exercise of a plan and its target."""

    exercise: str
    target: Target

    @classmethod
    def from_dict(cls, data: dict) -> PlannedExercise:
        """Read the stored shape: the target's keys plus "exercise"."""
        return cls(exercise=data["exercise"], target=Target.from_dict(data))

    def to_dict(self) -> dict:
        """The stored shape: the target's keys plus "exercise"."""
        return {"exercise": self.exercise, **self.target.to_dict()}


class SessionPlan(ValueObject):
    """What to do in one session; the first item is the warm-up, the last the cool-down."""

    title: str
    day_type: DayType
    items: tuple[PlannedExercise, ...]
    note: str = ""  # shown at the top of the lock screen

    @classmethod
    def from_dict(cls, data: dict) -> SessionPlan:
        """Read the stored shape {"title", "day_type", "plan": [...], "note"?}."""
        return cls(
            title=data["title"],
            day_type=data["day_type"],
            items=tuple(PlannedExercise.from_dict(i) for i in data.get("plan", [])),
            note=data.get("note", ""),
        )

    def to_dict(self) -> dict:
        """The stored shape."""
        data = {"title": self.title, "day_type": self.day_type, "plan": [i.to_dict() for i in self.items]}
        if self.note:
            data["note"] = self.note
        return data

    def with_note(self, note: str) -> SessionPlan:
        """The same plan with another note."""
        return self.model_copy(update={"note": note})

    def with_items(self, items: list[PlannedExercise]) -> SessionPlan:
        """The same plan with other exercises (e.g. trimmed to the lock's length)."""
        return self.model_copy(update={"items": tuple(items)})

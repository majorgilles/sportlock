"""A counted session as history reads it: the session, its exercises and their sets.

The coach, the calendar and the report each read it in their own JSON shape, built by the methods below.
"""

from __future__ import annotations

from sportlock.shared_kernel.base import ValueObject


class RecordedSet(ValueObject):
    """One set as stored: reps, seconds, load and the rest after it, each when known."""

    reps: int | None = None
    seconds: float | None = None
    load_kg: float | None = None
    rest_seconds: float | None = None


class RecordedExercise(ValueObject):
    """One exercise of a counted session, as stored."""

    exercise: str
    name: str
    kind: str
    status: str
    target: dict
    sets: tuple[RecordedSet, ...]
    rpe: int | None = None
    note: str | None = None
    skip_reason: str | None = None


class CountedSession(ValueObject):
    """A closed session that counts as training, with its exercises."""

    id: int
    day: str
    started_at: str | None
    finished_at: str
    kind: str
    status: str
    title: str | None = None
    day_type: str | None = None
    plan_source: str | None = None
    rpe: int | None = None
    notes: str | None = None
    exercises: tuple[RecordedExercise, ...] = ()

    def to_coach_dict(self) -> dict:
        """The session in full for the coach, without empty fields; swapped exercises included."""
        exercises = [
            _without_empty_values(
                {
                    "exercise": exercise.exercise,
                    "status": exercise.status,
                    "target": exercise.target,
                    "sets": [
                        {key: value for key, value in recorded.model_dump().items() if value is not None}
                        for recorded in exercise.sets
                    ],
                    "rpe": exercise.rpe,
                    "note": exercise.note,
                    "skip_reason": exercise.skip_reason,
                }
            )
            for exercise in self.exercises
        ]
        return _without_empty_values(
            {
                "id": self.id,
                "date": self.day,
                "started": self.started_at,
                "kind": self.kind,
                "day_type": self.day_type,
                "status": self.status,
                "rpe": self.rpe,
                "notes": self.notes,
                "exercises": exercises,
            }
        )

    def to_calendar_dict(self) -> dict:
        """The session for the calendar and the report; a swapped exercise is left out, its replacement follows."""
        return {
            "id": self.id,
            "day": self.day,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "kind": self.kind,
            "status": self.status,
            "title": self.title,
            "day_type": self.day_type,
            "plan_source": self.plan_source,
            "rpe": self.rpe,
            "notes": self.notes,
            "exercises": [
                {
                    "exercise": exercise.exercise,
                    "name": exercise.name,
                    "kind": exercise.kind,
                    "status": exercise.status,
                    "rpe": exercise.rpe,
                    "note": exercise.note or "",
                    "skip_reason": exercise.skip_reason or "",
                    "target": exercise.target,
                    "sets": [
                        {"reps": recorded.reps, "seconds": recorded.seconds, "load_kg": recorded.load_kg}
                        for recorded in exercise.sets
                    ],
                }
                for exercise in self.exercises
                if exercise.status != "swapped"
            ],
        }


def _without_empty_values(values: dict) -> dict:
    return {key: value for key, value in values.items() if value}

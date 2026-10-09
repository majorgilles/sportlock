"""A training session: the aggregate the lock screen drives, set by set.

A session moves through phases per exercise:

    ready ──start──▶ running ──stop──▶ logging ──save──▶ resting ──start──▶ running …
                                                   └─(target sets done)──▶ rating ──rate──▶ next exercise
    after the last exercise: summary ──finish──▶ (session finished, lock ends)

Set durations are measured from Start to Stop (after the get-ready countdown); rest is the gap
since the previous set ended.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Literal

from sportlock.exercises.domain.catalogue import Catalogue
from sportlock.shared_kernel.base import Aggregate, Entity
from sportlock.shared_kernel.errors import DomainError
from sportlock.shared_kernel.plans import SessionPlan
from sportlock.shared_kernel.targets import Target

type Phase = Literal["ready", "running", "logging", "resting", "rating", "summary"]
type SessionKind = Literal["scheduled", "manual", "test", "outside", "placeholder", "generated", "local"]
type SessionStatus = Literal["in_progress", "finished", "abandoned", "overridden"]
type ExerciseStatus = Literal["pending", "done", "skipped", "swapped"]


class TrainingError(DomainError):
    """The action doesn't fit the session's current state."""


class LoggedSet(Entity):
    """One set as it was done."""

    set_no: int
    reps: int | None
    seconds: float  # per side for holds done on each side
    load_kg: float | None
    rest_seconds: float | None
    started_at: datetime
    ended_at: datetime
    id: int | None = None


class SessionExercise(Entity):
    """One exercise of the session (an entity: its identity is the database id once saved)."""

    exercise: str
    name: str
    pattern: str
    kind: str
    target: Target
    status: ExerciseStatus = "pending"
    rpe: int | None = None
    note: str | None = None
    skip_reason: str | None = None
    swapped_to: SessionExercise | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    sets: list[LoggedSet] = []
    id: int | None = None


class TrainingSession(Aggregate):
    """The session aggregate root; every change goes through its behaviour methods."""

    day: date
    started_at: datetime
    kind: SessionKind
    lock_key: str | None
    title: str
    day_type: str
    plan_source: str  # generated | local
    exercises: list[SessionExercise]  # in the order they are done (a swap inserts its replacement)
    coach_note: str = ""
    status: SessionStatus = "in_progress"
    finished_at: datetime | None = None
    rpe: int | None = None
    notes: str = ""
    calories: int | None = None
    avg_hr: int | None = None
    body_weight: float | None = None
    phase: Phase = "ready"
    current: int = 0
    set_started_at: datetime | None = None
    set_ended_at: datetime | None = None
    last_set_end: datetime | None = None
    rest_until: datetime | None = None
    before_start: dict | None = None  # phase and rest to go back to when a lead-in is cancelled
    id: int | None = None

    @classmethod
    def begin(cls, plan: SessionPlan, catalogue: Catalogue, *, now: datetime, kind: SessionKind,
              lock_key: str | None, source: str) -> TrainingSession:
        """A new session from a (fitted) plan."""
        return cls(day=now.date(), started_at=now, kind=kind, lock_key=lock_key, title=plan.title,
                   day_type=plan.day_type, plan_source=source, coach_note=plan.note,
                   exercises=[_new_exercise(item.exercise, item.target, catalogue) for item in plan.items])

    # -- state -------------------------------------------------------------------------------

    @property
    def active(self) -> bool:
        """Whether the session is still being trained."""
        return self.status == "in_progress"

    @property
    def current_exercise(self) -> SessionExercise:
        """The exercise being done now."""
        return self.exercises[self.current]

    def in_lead_in(self, now: datetime) -> bool:
        """Whether a set was started but its get-ready countdown is still running."""
        return self.phase == "running" and self.set_started_at is not None and now < self.set_started_at

    def main_work_done(self) -> bool:
        """Every exercise except the first (warm-up) and last (cool-down) is done or was swapped
        for an easier one that is done."""
        main = self.exercises[1:-1] if len(self.exercises) > 2 else self.exercises
        return bool(main) and all(e.status in ("done", "swapped") for e in main)

    def _require(self, *phases: Phase) -> None:
        if not self.active:
            raise TrainingError("no session in progress")
        if phases and self.phase not in phases:
            raise TrainingError(f"can't do that while {self.phase}")

    # -- sets --------------------------------------------------------------------------------

    def start_set(self, now: datetime, lead_in: int = 0) -> None:
        """Start a set after a `lead_in`-second get-ready countdown; the set is timed from its end."""
        self._require("ready", "resting")
        exercise = self.current_exercise
        if exercise.started_at is None:
            exercise.started_at = now
        self.before_start = {"phase": self.phase, "rest_until": self.rest_until}
        self.phase, self.rest_until = "running", None
        self.set_started_at = now + timedelta(seconds=max(0, int(lead_in)))

    def go_now(self, now: datetime) -> None:
        """Skip the rest of the get-ready countdown."""
        self._require("running")
        if self.in_lead_in(now):
            self.set_started_at = now

    def cancel_set(self, now: datetime) -> None:
        """Back out of a set during its get-ready countdown; nothing is logged."""
        self._require("running")
        if not self.in_lead_in(now):
            raise TrainingError("the set is already running; stop it instead")
        before = self.before_start or {"phase": "ready", "rest_until": None}
        self.phase, self.rest_until, self.set_started_at, self.before_start = before["phase"], before["rest_until"], None, None

    def stop_set(self, now: datetime) -> None:
        """The set is over; its result is logged next."""
        self._require("running")
        if self.in_lead_in(now):
            raise TrainingError("the set hasn't started yet")
        self.phase, self.set_ended_at = "logging", now

    def save_set(self, now: datetime, reps: int | None = None, load_kg: float | None = None) -> None:
        """Log the stopped set, then rest (or rate the exercise after its last set)."""
        self._require("logging")
        exercise = self.current_exercise
        if exercise.kind == "reps" and (reps is None or int(reps) < 0):
            raise TrainingError("enter the reps you did")
        assert self.set_started_at is not None and self.set_ended_at is not None
        started, ended = self.set_started_at, self.set_ended_at
        seconds = (ended - started).total_seconds()
        if exercise.kind != "reps" and exercise.target.sides == "each":
            seconds /= 2  # one timer runs both sides; holds are logged per side
        exercise.sets.append(LoggedSet(
            set_no=len(exercise.sets) + 1, reps=None if exercise.kind != "reps" else int(reps),  # type: ignore[arg-type]
            seconds=seconds, load_kg=load_kg,
            rest_seconds=(started - self.last_set_end).total_seconds() if self.last_set_end else None,
            started_at=started, ended_at=ended))
        self.last_set_end = ended
        if len(exercise.sets) >= exercise.target.sets:
            self.phase = "rating"
        else:
            self.phase, self.rest_until = "resting", now + timedelta(seconds=exercise.target.rest)

    def end_sets(self, now: datetime) -> None:
        """Stop the exercise early (fewer sets than planned) and go rate it."""
        self._require("ready", "resting")
        if not self.current_exercise.sets:
            raise TrainingError("log at least one set, or skip the exercise")
        self.phase, self.rest_until = "rating", None

    # -- exercises ---------------------------------------------------------------------------

    def rate(self, now: datetime, rpe: int, note: str = "") -> None:
        """Effort for the exercise just done; moves on to the next one."""
        self._require("rating")
        if not 1 <= int(rpe) <= 10:
            raise TrainingError("effort must be between 1 and 10")
        exercise = self.current_exercise
        exercise.status, exercise.rpe, exercise.note, exercise.ended_at = "done", int(rpe), note, now
        self._advance()

    def skip(self, now: datetime, reason: str) -> None:
        """Skip the current exercise, with a reason the coach reads."""
        self._require("ready", "running", "logging", "resting", "rating")
        if len(reason.strip()) < 3:
            raise TrainingError("give a reason for skipping")
        exercise = self.current_exercise
        exercise.status, exercise.skip_reason, exercise.ended_at = "skipped", reason.strip(), now
        self._advance()

    def swap_easier(self, now: datetime, catalogue: Catalogue, laddered: set[str]) -> None:
        """Replace the current exercise with the easier variation on its (laddered) chain."""
        self._require("ready", "resting", "running")
        exercise = self.current_exercise
        spec = catalogue.find(exercise.exercise)
        easier = spec.easier if spec and spec.chain in laddered else None
        if not easier:
            raise TrainingError("no easier variation for this exercise")
        replacement = _new_exercise(easier, exercise.target.for_kind(catalogue.get(easier).kind), catalogue)
        exercise.status, exercise.swapped_to, exercise.ended_at = "swapped", replacement, now
        self.exercises.insert(self.current + 1, replacement)
        self._advance()

    def _advance(self) -> None:
        self.current += 1
        self.phase = "summary" if self.current >= len(self.exercises) else "ready"
        self.set_started_at = self.set_ended_at = self.last_set_end = self.rest_until = None

    # -- the end -----------------------------------------------------------------------------

    def finish(self, now: datetime, rpe: int, notes: str = "", calories: int | None = None,
               avg_hr: int | None = None, body_weight: float | None = None) -> None:
        """All exercises are through: rate the whole session; it counts as training."""
        self._require("summary")
        if not 1 <= int(rpe) <= 10:
            raise TrainingError("effort must be between 1 and 10")
        self.status, self.finished_at, self.rpe, self.notes = "finished", now, int(rpe), notes
        self.calories, self.avg_hr, self.body_weight = calories, avg_hr, body_weight

    def close(self, now: datetime, status: SessionStatus) -> None:
        """End an unfinished session (abandoned when time ran out, overridden). When time ran out
        but all the main work was done (only the cool-down, or less, left), it counts as finished."""
        if not self.active:
            return
        if status == "abandoned" and self.main_work_done():
            status, self.notes = "finished", "Time ran out after the main work; counted as a session."
        self.status, self.finished_at = status, now


def _new_exercise(exercise_id: str, target: Target, catalogue: Catalogue) -> SessionExercise:
    spec = catalogue.get(exercise_id)
    return SessionExercise(exercise=exercise_id, name=spec.name, pattern=spec.pattern, kind=spec.kind,
                           target=target.with_(sides=spec.sides))  # reps and seconds count per side

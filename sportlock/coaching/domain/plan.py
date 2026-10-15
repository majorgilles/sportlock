"""The coach's plan.

Two versions of the next session, advice for the next lock, answers to the athlete's feedback, ladder overrides and
memory changes. `CoachPlan.parse` checks everything the coach wrote before any of it is used.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from sportlock.coaching.domain.memory import TOPICS, MemoryOperation
from sportlock.exercises.domain.catalogue import Catalogue, Exercise
from sportlock.shared_kernel.base import ValueObject
from sportlock.shared_kernel.errors import DomainError
from sportlock.shared_kernel.plans import PlannedExercise, SessionPlan
from sportlock.shared_kernel.targets import Target

MIN_SETS, MAX_SETS = 1, 6
MAX_REST_SECONDS = 300
MAX_REPS = 30
MIN_SECONDS, MAX_SECONDS = 5, 900
MIN_EXERCISES = 2  # the warm-up and the cool-down
MIN_RECOVERY_MINUTES, MAX_RECOVERY_MINUTES = 5, 60

HARD_GAP = timedelta(hours=48)


class CoachOutputError(DomainError):
    """The coach's answer can't be used; the built-in planner takes over."""


class NextLockAdvice(ValueObject):
    """What the coach advises for the next scheduled lock."""

    mode: Literal["auto", "recovery", "rest"] = "auto"
    recovery_minutes: int | None = None
    reason: str = ""


class Recommendation(ValueObject):
    """An answer to something the athlete said or showed in the last session."""

    about: str
    advice: str


class LadderOverride(ValueObject):
    """The coach disagrees with where the rules put a chain."""

    chain: str
    exercise: str
    target: Target
    reason: str


class CoachPlan(ValueObject):
    """A validated plan, as stored until the next session makes it stale."""

    rationale: str
    hard: SessionPlan
    recovery: SessionPlan
    next_lock: NextLockAdvice = NextLockAdvice()
    recommendations: tuple[Recommendation, ...] = ()
    feedback_session: int | None = None  # the session the recommendations answer
    basis: str = ""  # the data it was written from (see `basis` in RunCoachService)
    generated_at: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> CoachPlan:
        """Read the stored JSON shape."""
        return cls(
            rationale=data.get("rationale", ""),
            hard=SessionPlan.from_dict(data["hard"]),
            recovery=SessionPlan.from_dict(data["recovery"]),
            next_lock=NextLockAdvice(**data.get("next_lock") or {}),
            recommendations=tuple(Recommendation(**r) for r in data.get("recommendations", [])),
            feedback_session=data.get("feedback_session"),
            basis=data.get("basis", ""),
            generated_at=data.get("generated_at", ""),
        )

    def to_dict(self) -> dict:
        """The stored JSON shape."""
        return {
            "rationale": self.rationale,
            "hard": self.hard.to_dict(),
            "recovery": self.recovery.to_dict(),
            "next_lock": self.next_lock.model_dump(),
            "recommendations": [r.model_dump() for r in self.recommendations],
            "feedback_session": self.feedback_session,
            "basis": self.basis,
            "generated_at": self.generated_at,
        }

    def keeping(self, replaced: str, kept: str, target: Target, note: str) -> CoachPlan:
        """The plan with `kept` back in place of `replaced`, in both versions.

        The athlete refused the replacement.
        """

        def swap(version: SessionPlan) -> SessionPlan:
            items = [
                PlannedExercise(exercise=kept, target=target.with_(progress=note)) if i.exercise == replaced else i
                for i in version.items
            ]
            return version.with_items(items)

        return self.model_copy(update={"hard": swap(self.hard), "recovery": swap(self.recovery)})

    def version_for(self, mode: str | None, *, last_hard: datetime | None, now: datetime) -> SessionPlan:
        """The hard or recovery version: as decided for the lock, else by the 48-hour rule."""
        if mode == "hard":
            return self.hard
        if mode == "recovery" or (mode is None and last_hard is not None and now - last_hard < HARD_GAP):
            return self.recovery
        return self.hard


class ParsedCoachOutput(ValueObject):
    """Everything the coach wrote, validated."""

    plan: CoachPlan
    overrides: tuple[LadderOverride, ...]
    memory: tuple[MemoryOperation, ...]


def _target(raw: dict, label: str, exercise: Exercise, sets: int, rest: int) -> Target:
    """The planned target in the exercise's own kind: a rep range, or seconds."""
    note = str(raw.get("note", "")).strip() or None
    if exercise.kind == "reps":
        lo, hi = raw.get("reps_low"), raw.get("reps_high")
        if not (isinstance(lo, int) and isinstance(hi, int) and 1 <= lo <= hi <= MAX_REPS):
            raise CoachOutputError(f"{label}: {exercise.id} has reps {lo!r}–{hi!r}")
        return Target(sets=sets, rest=rest, reps=(lo, hi), progress=note)
    seconds = raw.get("seconds")
    if not isinstance(seconds, int) or not MIN_SECONDS <= seconds <= MAX_SECONDS:
        raise CoachOutputError(f"{label}: {exercise.id} has {seconds!r} seconds")
    return Target(sets=sets, rest=rest, seconds=seconds, progress=note)


def _planned_exercise(raw: dict, label: str, catalogue: Catalogue, equipment: set[str]) -> PlannedExercise:
    """One exercise the coach planned, checked against the catalogue and the athlete's equipment."""
    exercise_id = raw.get("exercise")
    exercise = catalogue.find(exercise_id) if isinstance(exercise_id, str) else None
    if exercise is None:
        raise CoachOutputError(f"{label}: unknown exercise {exercise_id!r}")
    missing = exercise.equipment - equipment
    if missing:
        raise CoachOutputError(f"{label}: {exercise.id} needs {', '.join(sorted(missing))}")
    sets, rest = raw.get("sets"), raw.get("rest")
    if not isinstance(sets, int) or not MIN_SETS <= sets <= MAX_SETS:
        raise CoachOutputError(f"{label}: {exercise.id} has {sets!r} sets")
    if not isinstance(rest, int) or not 0 <= rest <= MAX_REST_SECONDS:
        raise CoachOutputError(f"{label}: {exercise.id} has rest {rest!r}")
    return PlannedExercise(exercise=exercise.id, target=_target(raw, label, exercise, sets, rest))


def _version(raw: dict, label: str, day_type: str, catalogue: Catalogue, equipment: set[str]) -> SessionPlan:
    """One version of the next session: the hard one or the recovery one."""
    items = tuple(_planned_exercise(i, label, catalogue, equipment) for i in raw.get("exercises", []))
    if len(items) < MIN_EXERCISES:
        raise CoachOutputError(f"{label} plan has fewer than 2 exercises")
    title = str(raw.get("title", "")).strip() or ("Full body" if label == "hard" else "Recovery")
    return SessionPlan(title=title, day_type=day_type, items=items)  # type: ignore[arg-type]


def _ladder_overrides(
    output: dict, catalogue: Catalogue, equipment: set[str], laddered: set[str]
) -> list[LadderOverride]:
    """The ladder positions the coach moves by hand, each on its own chain and with a reason."""
    overrides = []
    for raw in output.get("ladder_overrides", []):
        chain = raw.get("chain")
        if chain not in laddered:
            raise CoachOutputError(f"override for unknown chain {chain!r}")
        planned = _planned_exercise(raw, "override", catalogue, equipment)
        if catalogue.get(planned.exercise).chain != chain:
            raise CoachOutputError(f"override puts {planned.exercise} on the {chain} chain")
        if not str(raw.get("reason", "")).strip():
            raise CoachOutputError("override without a reason")
        overrides.append(
            LadderOverride(
                chain=chain,
                exercise=planned.exercise,
                target=planned.target.with_(progress=None),
                reason=raw["reason"].strip(),
            )
        )
    return overrides


def _next_lock_advice(output: dict) -> NextLockAdvice:
    """What the coach advises for the next lock: decide automatically, recovery, or rest."""
    advice = output.get("next_lock") or {}
    if advice.get("mode", "auto") not in ("auto", "recovery", "rest"):
        raise CoachOutputError(f"next_lock mode {advice.get('mode')!r}")
    minutes = advice.get("recovery_minutes")
    if minutes is not None and (
        not isinstance(minutes, int) or not MIN_RECOVERY_MINUTES <= minutes <= MAX_RECOVERY_MINUTES
    ):
        raise CoachOutputError(f"next_lock recovery_minutes {minutes!r}")
    return NextLockAdvice(
        mode=advice.get("mode", "auto"), recovery_minutes=minutes, reason=str(advice.get("reason", "")).strip()
    )


def _memory_operations(output: dict) -> list[MemoryOperation]:
    """The coach's changes to its memory; anything that is not add, update or delete is ignored."""
    return [
        MemoryOperation(
            op=m["op"], id=m.get("id"), topic=m.get("topic") if m.get("topic") in TOPICS else None, text=m.get("note")
        )
        for m in output.get("memory") or []
        if isinstance(m, dict) and m.get("op") in ("add", "update", "delete")
    ]


def parse_coach_output(
    output: dict, catalogue: Catalogue, equipment: set[str], laddered: set[str]
) -> ParsedCoachOutput:
    """Validate the coach's JSON answer. Raises CoachOutputError."""
    hard = _version(output["hard"], "hard", "hard", catalogue, equipment)
    recovery = _version(
        output["recovery"], "recovery", output["recovery"].get("day_type", "mobility"), catalogue, equipment
    )
    overrides = _ladder_overrides(output, catalogue, equipment, laddered)
    next_lock = _next_lock_advice(output)
    recommendations = [
        Recommendation(about=str(r.get("about", "")).strip(), advice=str(r.get("advice", "")).strip())
        for r in output.get("recommendations") or []
    ]
    memory = _memory_operations(output)
    plan = CoachPlan(
        rationale=str(output.get("rationale", "")).strip(),
        hard=hard,
        recovery=recovery,
        next_lock=next_lock,
        recommendations=tuple(r for r in recommendations if r.advice)[:4],
    )
    return ParsedCoachOutput(plan=plan, overrides=tuple(overrides), memory=tuple(memory))

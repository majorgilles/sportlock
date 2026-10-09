"""The coach's plan: two versions of the next session, advice for the next lock, answers to the
athlete's feedback, ladder overrides and memory changes. `CoachPlan.parse` checks everything
the coach wrote before any of it is used."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from sportlock.coaching.domain.memory import TOPICS, MemoryOperation
from sportlock.exercises.domain.catalogue import Catalogue
from sportlock.shared_kernel.base import ValueObject
from sportlock.shared_kernel.errors import DomainError
from sportlock.shared_kernel.plans import PlannedExercise, SessionPlan
from sportlock.shared_kernel.targets import Target

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
        return cls(rationale=data.get("rationale", ""), hard=SessionPlan.from_dict(data["hard"]),
                   recovery=SessionPlan.from_dict(data["recovery"]),
                   next_lock=NextLockAdvice(**data.get("next_lock") or {}),
                   recommendations=tuple(Recommendation(**r) for r in data.get("recommendations", [])),
                   feedback_session=data.get("feedback_session"), basis=data.get("basis", ""),
                   generated_at=data.get("generated_at", ""))

    def to_dict(self) -> dict:
        """The stored JSON shape."""
        return {"rationale": self.rationale, "hard": self.hard.to_dict(), "recovery": self.recovery.to_dict(),
                "next_lock": self.next_lock.model_dump(), "recommendations": [r.model_dump() for r in self.recommendations],
                "feedback_session": self.feedback_session, "basis": self.basis, "generated_at": self.generated_at}

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


def parse_coach_output(output: dict, catalogue: Catalogue, equipment: set[str], laddered: set[str]) -> ParsedCoachOutput:
    """Validate the coach's JSON answer. Raises CoachOutputError."""
    def item(raw: dict, label: str) -> PlannedExercise:
        exercise_id = raw.get("exercise")
        exercise = catalogue.find(exercise_id) if isinstance(exercise_id, str) else None
        if exercise is None:
            raise CoachOutputError(f"{label}: unknown exercise {exercise_id!r}")
        missing = exercise.equipment - equipment
        if missing:
            raise CoachOutputError(f"{label}: {exercise.id} needs {', '.join(sorted(missing))}")
        sets, rest = raw.get("sets"), raw.get("rest")
        if not isinstance(sets, int) or not 1 <= sets <= 6:
            raise CoachOutputError(f"{label}: {exercise.id} has {sets!r} sets")
        if not isinstance(rest, int) or not 0 <= rest <= 300:
            raise CoachOutputError(f"{label}: {exercise.id} has rest {rest!r}")
        note = str(raw.get("note", "")).strip() or None
        if exercise.kind == "reps":
            lo, hi = raw.get("reps_low"), raw.get("reps_high")
            if not (isinstance(lo, int) and isinstance(hi, int) and 1 <= lo <= hi <= 30):
                raise CoachOutputError(f"{label}: {exercise.id} has reps {lo!r}–{hi!r}")
            return PlannedExercise(exercise=exercise.id, target=Target(sets=sets, rest=rest, reps=(lo, hi), progress=note))
        seconds = raw.get("seconds")
        if not isinstance(seconds, int) or not 5 <= seconds <= 900:
            raise CoachOutputError(f"{label}: {exercise.id} has {seconds!r} seconds")
        return PlannedExercise(exercise=exercise.id, target=Target(sets=sets, rest=rest, seconds=seconds, progress=note))

    def version(raw: dict, label: str, day_type: str) -> SessionPlan:
        items = tuple(item(i, label) for i in raw.get("exercises", []))
        if len(items) < 2:
            raise CoachOutputError(f"{label} plan has fewer than 2 exercises")
        title = str(raw.get("title", "")).strip() or ("Full body" if label == "hard" else "Recovery")
        return SessionPlan(title=title, day_type=day_type, items=items)  # type: ignore[arg-type]

    hard = version(output["hard"], "hard", "hard")
    recovery = version(output["recovery"], "recovery", output["recovery"].get("day_type", "mobility"))

    overrides = []
    for raw in output.get("ladder_overrides", []):
        chain = raw.get("chain")
        if chain not in laddered:
            raise CoachOutputError(f"override for unknown chain {chain!r}")
        planned = item(raw, "override")
        if catalogue.get(planned.exercise).chain != chain:
            raise CoachOutputError(f"override puts {planned.exercise} on the {chain} chain")
        if not str(raw.get("reason", "")).strip():
            raise CoachOutputError("override without a reason")
        overrides.append(LadderOverride(chain=chain, exercise=planned.exercise, target=planned.target.with_(progress=None),
                                        reason=raw["reason"].strip()))

    advice = output.get("next_lock") or {}
    if advice.get("mode", "auto") not in ("auto", "recovery", "rest"):
        raise CoachOutputError(f"next_lock mode {advice.get('mode')!r}")
    minutes = advice.get("recovery_minutes")
    if minutes is not None and (not isinstance(minutes, int) or not 5 <= minutes <= 60):
        raise CoachOutputError(f"next_lock recovery_minutes {minutes!r}")

    recommendations = [Recommendation(about=str(r.get("about", "")).strip(), advice=str(r.get("advice", "")).strip())
                       for r in output.get("recommendations") or []]
    memory = [MemoryOperation(op=m["op"], id=m.get("id"), topic=m.get("topic") if m.get("topic") in TOPICS else None,
                              text=m.get("note"))
              for m in output.get("memory") or [] if isinstance(m, dict) and m.get("op") in ("add", "update", "delete")]

    plan = CoachPlan(rationale=str(output.get("rationale", "")).strip(), hard=hard, recovery=recovery,
                     next_lock=NextLockAdvice(mode=advice.get("mode", "auto"), recovery_minutes=minutes,
                                              reason=str(advice.get("reason", "")).strip()),
                     recommendations=tuple(r for r in recommendations if r.advice)[:4])
    return ParsedCoachOutput(plan=plan, overrides=tuple(overrides), memory=tuple(memory))

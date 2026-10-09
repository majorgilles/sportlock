"""The built-in planner: a session straight from the ladders, used when the coach has no fresh plan."""

from __future__ import annotations

from datetime import datetime, timedelta

from sportlock.exercises.domain.catalogue import Catalogue
from sportlock.progression.domain.ladders import LadderPosition
from sportlock.shared_kernel.plans import PlannedExercise, SessionPlan
from sportlock.shared_kernel.targets import Target

HARD_GAP = timedelta(hours=48)
WARMUP = PlannedExercise(exercise="dynamic-warmup", target=Target(sets=1, rest=0, seconds=300))
COOLDOWN = PlannedExercise(exercise="static-stretch", target=Target(sets=1, rest=0, seconds=300))
HARD_DAY = ("push-horizontal", "squat", "pull-horizontal", "hinge", "core")
MOBILITY_DAY = (
    PlannedExercise(exercise="cat-cow", target=Target(sets=2, rest=15, reps=(8, 10))),
    PlannedExercise(exercise="worlds-greatest-stretch", target=Target(sets=2, rest=15, reps=(4, 6))),
    PlannedExercise(exercise="hip-flexor-stretch", target=Target(sets=2, rest=15, seconds=30)),
    PlannedExercise(exercise="hamstring-stretch", target=Target(sets=2, rest=15, seconds=30)),
    PlannedExercise(exercise="chest-doorway-stretch", target=Target(sets=2, rest=15, seconds=30)),
)


def is_recovery_day(mode: str | None, last_hard: datetime | None, now: datetime) -> bool:
    """Recovery when decided so, or (undecided) within 48 h of a hard session."""
    return mode == "recovery" or (mode is None and last_hard is not None and now - last_hard < HARD_GAP)


def local_plan(positions: dict[str, LadderPosition], catalogue: Catalogue, equipment: set[str], *,
               recovery: bool, hours_since_hard: int | None) -> SessionPlan:
    """A hard full-body day from the ladders, or a mobility day."""
    if recovery:
        note = f"Hard session {hours_since_hard} h ago" if hours_since_hard is not None else "Recovery day"
        items = [i for i in MOBILITY_DAY if catalogue.get(i.exercise).available_with(equipment)]
        return SessionPlan(title="Mobility & recovery", day_type="mobility", items=(WARMUP, *items, COOLDOWN), note=note)

    items = [WARMUP]
    for chain in HARD_DAY:
        position = positions[chain]
        exercise = catalogue.easiest_available(position.exercise, equipment)
        if exercise is None:
            continue
        if exercise != position.exercise:
            why = f"{catalogue.name(position.exercise)} needs equipment you don't have"
        else:
            why = position.reason
        items.append(PlannedExercise(exercise=exercise, target=position.target.with_(progress=why or None)))
    items.append(COOLDOWN)
    return SessionPlan(title="Full body", day_type="hard", items=tuple(items))


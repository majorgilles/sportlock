"""Use cases of a training session.

Begin it when a lock starts, record what the athlete does on the lock screen, close it when the lock ends.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Literal

from sportlock.coaching.application.fresh_plan import CoachPlanFreshness
from sportlock.exercises.domain.catalogue import Catalogue
from sportlock.progression.application.apply_session_progression_service import ApplySessionProgressionService
from sportlock.progression.domain.ladders import START, LadderMove, LadderRepositoryProtocol, positions
from sportlock.progression.domain.planner import is_recovery_day, local_plan
from sportlock.recovery.domain.policy import pace_factor, transition_seconds
from sportlock.shared_kernel.base import ValueObject
from sportlock.shared_kernel.plans import SessionPlan
from sportlock.training.domain.fitting import fit_plan
from sportlock.training.domain.repositories import TrainingHistoryProtocol, TrainingSessionRepositoryProtocol
from sportlock.training.domain.session import SessionKind, TrainingError, TrainingSession

HISTORY_FOR_PACE = 10


class BeginTrainingSessionCommand(ValueObject):
    """A lock started: serve a session that fits it. Handled by `BeginTrainingSessionService`."""

    kind: SessionKind
    lock_key: str | None
    minutes: float  # time left in the lock
    equipment: frozenset[str]
    mode: Literal["hard", "recovery"] | None = None  # as decided for a scheduled lock


class BeginTrainingSessionService:
    """Picks the coach's fresh plan (or the built-in one), fits it to the lock and starts it."""

    def __init__(
        self,
        training_session_repository: TrainingSessionRepositoryProtocol,
        training_history: TrainingHistoryProtocol,
        ladder_repository: LadderRepositoryProtocol,
        coach_plan_freshness: CoachPlanFreshness,
        catalogue: Catalogue,
    ) -> None:
        self.training_session_repository = training_session_repository
        self.training_history = training_history
        self.ladder_repository = ladder_repository
        self.coach_plan_freshness = coach_plan_freshness
        self.catalogue = catalogue

    def execute(self, command: BeginTrainingSessionCommand, now: datetime) -> TrainingSession:
        """The active session (an already running one is kept)."""
        if (active := self.training_session_repository.active()) is not None:
            return active
        last_hard = self.training_history.last_hard()
        coach_plan = self.coach_plan_freshness.fresh_plan()
        if coach_plan:
            plan = coach_plan.version_for(command.mode, last_hard=last_hard, now=now).with_note(coach_plan.rationale)
            source = "generated"
        else:
            hours = int((now - last_hard).total_seconds() // 3600) if last_hard else None
            plan = local_plan(
                {p.chain: p for p in positions(self.ladder_repository.saved())},
                self.catalogue,
                set(command.equipment),
                recovery=is_recovery_day(command.mode, last_hard, now),
                hours_since_hard=hours,
            )
            source = "local"
        plan = self._with_sides(plan)
        plan = fit_plan(
            plan,
            command.minutes,
            pace=pace_factor(self.training_history.exercise_timings(HISTORY_FOR_PACE)),
            transition=transition_seconds(self.training_history.transition_gaps(HISTORY_FOR_PACE)),
        )
        session = TrainingSession.begin(
            plan, self.catalogue, now=now, kind=command.kind, lock_key=command.lock_key, source=source
        )
        return self.training_session_repository.add(session)

    def _with_sides(self, plan: SessionPlan) -> SessionPlan:
        """Both sides count in the time estimate."""
        return plan.with_items(
            [
                i.model_copy(update={"target": i.target.with_(sides=self.catalogue.get(i.exercise).sides)})
                for i in plan.items
            ]
        )


type TrainingAction = Literal[
    "start_set", "stop_set", "end_sets", "swap_easier", "go_now", "cancel_set", "save_set", "rate", "skip", "finish"
]


class RecordTrainingActionCommand(ValueObject):
    """Something the athlete did on the lock screen. Handled by `RecordTrainingActionService`."""

    action: TrainingAction
    reps: int | None = None
    load_kg: float | None = None
    rpe: int | None = None
    note: str = ""
    reason: str = ""
    notes: str = ""
    calories: int | None = None
    avg_hr: int | None = None
    body_weight: float | None = None
    lead_in: int = 0


TrainingAction = Callable[[TrainingSession, "RecordTrainingActionCommand", datetime, Catalogue], None]
TRAINING_ACTIONS: dict[str, TrainingAction] = {
    "start_set": lambda session, command, now, catalogue: session.start_set(now, command.lead_in),
    "go_now": lambda session, command, now, catalogue: session.go_now(now),
    "cancel_set": lambda session, command, now, catalogue: session.cancel_set(now),
    "stop_set": lambda session, command, now, catalogue: session.stop_set(now),
    "save_set": lambda session, command, now, catalogue: session.save_set(now, command.reps, command.load_kg),
    "end_sets": lambda session, command, now, catalogue: session.end_sets(now),
    "rate": lambda session, command, now, catalogue: session.rate(now, _required(command.rpe), command.note),
    "skip": lambda session, command, now, catalogue: session.skip(now, command.reason),
    "swap_easier": lambda session, command, now, catalogue: session.swap_easier(now, catalogue, set(START)),
    "finish": lambda session, command, now, catalogue: session.finish(
        now, _required(command.rpe), command.notes, command.calories, command.avg_hr, command.body_weight
    ),
}


class RecordTrainingActionService:
    """Applies one action to the active session; a finished session moves the ladders."""

    def __init__(
        self,
        training_session_repository: TrainingSessionRepositoryProtocol,
        catalogue: Catalogue,
        apply_session_progression_service: ApplySessionProgressionService,
    ) -> None:
        self.training_session_repository = training_session_repository
        self.catalogue = catalogue
        self.apply_session_progression_service = apply_session_progression_service

    def execute(self, command: RecordTrainingActionCommand, now: datetime) -> tuple[TrainingSession, list[LadderMove]]:
        """The session after the action, and the ladder moves when it finished. Raises TrainingError."""
        session = self.training_session_repository.active()
        if session is None:
            raise TrainingError("no session in progress")
        action = TRAINING_ACTIONS.get(command.action)
        if action:
            action(session, command, now, self.catalogue)
        self.training_session_repository.save(session)
        moves = self.apply_session_progression_service.execute(session, now) if not session.active else []
        return session, moves


class CloseTrainingSessionService:
    """The lock ended before the session did: abandoned (time ran out) or overridden."""

    def __init__(
        self,
        training_session_repository: TrainingSessionRepositoryProtocol,
        apply_session_progression_service: ApplySessionProgressionService,
    ) -> None:
        self.training_session_repository = training_session_repository
        self.apply_session_progression_service = apply_session_progression_service

    def execute(self, status: Literal["abandoned", "overridden"], now: datetime) -> None:
        """No-op without an active session."""
        session = self.training_session_repository.active()
        if session is None:
            return
        session.close(now, status)
        self.training_session_repository.save(session)
        self.apply_session_progression_service.execute(session, now)


def _required(rpe: int | None) -> int:
    if rpe is None:
        raise TrainingError("effort must be between 1 and 10")
    return rpe

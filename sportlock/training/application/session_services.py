"""Use cases of a training session: begin it when a lock starts, record what the athlete does on the
lock screen, close it when the lock ends."""

from __future__ import annotations

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

    def __init__(self, sessions: TrainingSessionRepositoryProtocol, history: TrainingHistoryProtocol,
                 ladders: LadderRepositoryProtocol, freshness: CoachPlanFreshness, catalogue: Catalogue) -> None:
        self.sessions = sessions
        self.history = history
        self.ladders = ladders
        self.freshness = freshness
        self.catalogue = catalogue

    def execute(self, command: BeginTrainingSessionCommand, now: datetime) -> TrainingSession:
        """The active session (an already running one is kept)."""
        if (active := self.sessions.active()) is not None:
            return active
        last_hard = self.history.last_hard()
        coach_plan = self.freshness.fresh_plan()
        if coach_plan:
            plan = coach_plan.version_for(command.mode, last_hard=last_hard, now=now).with_note(coach_plan.rationale)
            source = "generated"
        else:
            hours = int((now - last_hard).total_seconds() // 3600) if last_hard else None
            plan = local_plan({p.chain: p for p in positions(self.ladders.saved())}, self.catalogue, set(command.equipment),
                              recovery=is_recovery_day(command.mode, last_hard, now), hours_since_hard=hours)
            source = "local"
        plan = self._with_sides(plan)
        plan = fit_plan(plan, command.minutes, pace=pace_factor(self.history.exercise_timings(HISTORY_FOR_PACE)),
                        transition=transition_seconds(self.history.transition_gaps(HISTORY_FOR_PACE)))
        session = TrainingSession.begin(plan, self.catalogue, now=now, kind=command.kind, lock_key=command.lock_key,
                                        source=source)
        return self.sessions.add(session)

    def _with_sides(self, plan: SessionPlan) -> SessionPlan:
        """Both sides count in the time estimate."""
        return plan.with_items([i.model_copy(update={"target": i.target.with_(sides=self.catalogue.get(i.exercise).sides)})
                                for i in plan.items])


type TrainingAction = Literal["start_set", "stop_set", "end_sets", "swap_easier", "go_now", "cancel_set", "save_set",
                              "rate", "skip", "finish"]


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


class RecordTrainingActionService:
    """Applies one action to the active session; a finished session moves the ladders."""

    def __init__(self, sessions: TrainingSessionRepositoryProtocol, catalogue: Catalogue,
                 progression: ApplySessionProgressionService) -> None:
        self.sessions = sessions
        self.catalogue = catalogue
        self.progression = progression

    def execute(self, command: RecordTrainingActionCommand, now: datetime) -> tuple[TrainingSession, list[LadderMove]]:
        """The session after the action, and the ladder moves when it finished. Raises TrainingError."""
        session = self.sessions.active()
        if session is None:
            raise TrainingError("no session in progress")
        match command.action:
            case "start_set":
                session.start_set(now, command.lead_in)
            case "go_now":
                session.go_now(now)
            case "cancel_set":
                session.cancel_set(now)
            case "stop_set":
                session.stop_set(now)
            case "save_set":
                session.save_set(now, command.reps, command.load_kg)
            case "end_sets":
                session.end_sets(now)
            case "rate":
                session.rate(now, _required(command.rpe), command.note)
            case "skip":
                session.skip(now, command.reason)
            case "swap_easier":
                session.swap_easier(now, self.catalogue, set(START))
            case "finish":
                session.finish(now, _required(command.rpe), command.notes, command.calories, command.avg_hr,
                               command.body_weight)
        self.sessions.save(session)
        moves = self.progression.execute(session, now) if not session.active else []
        return session, moves


class CloseTrainingSessionService:
    """The lock ended before the session did: abandoned (time ran out) or overridden."""

    def __init__(self, sessions: TrainingSessionRepositoryProtocol, progression: ApplySessionProgressionService) -> None:
        self.sessions = sessions
        self.progression = progression

    def execute(self, status: Literal["abandoned", "overridden"], now: datetime) -> None:
        """No-op without an active session."""
        session = self.sessions.active()
        if session is None:
            return
        session.close(now, status)
        self.sessions.save(session)
        self.progression.execute(session, now)


def _required(rpe: int | None) -> int:
    if rpe is None:
        raise TrainingError("effort must be between 1 and 10")
    return rpe

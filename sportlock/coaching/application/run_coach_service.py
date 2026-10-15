"""Plan the next session with the coach.

Runs after every session and every profile change, in the background: it can take minutes.
"""

from __future__ import annotations

from datetime import datetime

from sportlock.coaching.application.coach_context_service import BuildCoachContextService
from sportlock.coaching.application.fresh_plan import CoachPlanFreshness
from sportlock.coaching.application.run_coach_command import RunCoachCommand
from sportlock.coaching.domain.plan import (
    CoachOutputError,
    CoachPlan,
    LadderOverride,
    ParsedCoachOutput,
    parse_coach_output,
)
from sportlock.coaching.domain.ports import (
    CoachMemoryRepositoryProtocol,
    CoachPlanRepositoryProtocol,
    CoachProtocol,
    CoachReviewRepositoryProtocol,
    CoachRun,
    CoachRunRepositoryProtocol,
    CoachUnavailableError,
)
from sportlock.coaching.domain.review import CoachReview
from sportlock.exercises.domain.catalogue import Catalogue
from sportlock.progression.domain.ladders import (
    START,
    LadderMove,
    LadderMoveRecord,
    LadderMoveRecordFilters,
    LadderMoveRecordRepositoryProtocol,
    LadderPosition,
    LadderPositionFilters,
    LadderPositionRepositoryProtocol,
    positions,
)
from sportlock.progression.domain.rules import Proposal
from sportlock.shared_kernel.base import ValueObject
from sportlock.shared_kernel.ports import ClockProtocol, DesktopProtocol
from sportlock.shared_kernel.time import iso
from sportlock.training.application.training_history import TrainingHistory


class CoachRequest(ValueObject):
    """A prepared coach run: when it started, the data the plan is based on, and the coach's context."""

    started: datetime
    basis: str
    context: dict


class PrepareCoachRunService:
    """Notes the start and the plan's basis before the coach runs, since a session can end meanwhile."""

    def __init__(
        self,
        clock: ClockProtocol,
        coach_plan_freshness: CoachPlanFreshness,
        build_coach_context_service: BuildCoachContextService,
    ) -> None:
        self.clock = clock
        self.coach_plan_freshness = coach_plan_freshness
        self.build_coach_context_service = build_coach_context_service

    def execute(self, command: RunCoachCommand) -> CoachRequest:
        """The request to send to the coach."""
        started = self.clock.now()
        basis = self.coach_plan_freshness.basis()
        return CoachRequest(
            started=started, basis=basis, context=self.build_coach_context_service.execute(started, command)
        )


class LogCoachRunService:
    """Records each coach run, successful or not, with how long it took."""

    def __init__(self, coach_run_repository: CoachRunRepositoryProtocol, clock: ClockProtocol) -> None:
        self.coach_run_repository = coach_run_repository
        self.clock = clock

    def execute(self, started: datetime, error: str | None = None) -> int:
        """The run's id."""
        seconds = round((self.clock.now() - started).total_seconds())
        run = CoachRun(at=iso(started), seconds=seconds, ok=error is None, error=error)
        return self.coach_run_repository.create(run).id


class ApplyLadderOverridesService:
    """Moves the ladders the coach overrode, recording each move as the coach's decision."""

    def __init__(
        self,
        ladder_position_repository: LadderPositionRepositoryProtocol,
        ladder_move_record_repository: LadderMoveRecordRepositoryProtocol,
    ) -> None:
        self.ladder_position_repository = ladder_position_repository
        self.ladder_move_record_repository = ladder_move_record_repository

    def execute(self, overrides: tuple[LadderOverride, ...], last_session: int | None, now: datetime) -> None:
        """Apply the overrides in order."""
        current = {p.chain: p for p in positions(self.ladder_position_repository.find(LadderPositionFilters()))}
        for override in overrides:
            before = current[override.chain]
            applied = LadderMoveRecordFilters(chain=override.chain, status="applied", latest=1)
            for record in self.ladder_move_record_repository.find(applied):
                self.ladder_move_record_repository.update(record.overridden(override.reason))
            self.ladder_move_record_repository.create(
                LadderMoveRecord(
                    session_id=last_session or 0,
                    move=LadderMove(
                        chain=override.chain,
                        session_exercise_id=0,
                        from_exercise=before.exercise,
                        from_target=before.target,
                        proposal=Proposal(
                            rule="override", exercise=override.exercise, target=override.target, reason=override.reason
                        ),
                    ),
                    decided_by="agent",
                    decided_at=now,
                )
            )
            self.ladder_position_repository.update(
                LadderPosition(
                    chain=override.chain,
                    exercise=override.exercise,
                    target=override.target,
                    reason=f"Coach: {override.reason}",
                    updated_at=now,
                )
            )


class StoreCoachPlanService:
    """Stores what the coach wrote: ladder overrides, memory changes, the plan, and its answer to feedback."""

    def __init__(
        self,
        apply_ladder_overrides_service: ApplyLadderOverridesService,
        training_history: TrainingHistory,
        coach_memory_repository: CoachMemoryRepositoryProtocol,
        coach_plan_repository: CoachPlanRepositoryProtocol,
        coach_review_repository: CoachReviewRepositoryProtocol,
        clock: ClockProtocol,
    ) -> None:
        self.apply_ladder_overrides_service = apply_ladder_overrides_service
        self.training_history = training_history
        self.coach_memory_repository = coach_memory_repository
        self.coach_plan_repository = coach_plan_repository
        self.coach_review_repository = coach_review_repository
        self.clock = clock

    def execute(self, parsed: ParsedCoachOutput, request: CoachRequest, run_id: int) -> CoachPlan:
        """The stored plan."""
        last_session = self.training_history.last_counted_id()
        plan = parsed.plan.model_copy(
            update={
                "basis": request.basis,
                "generated_at": iso(request.started),
                "feedback_session": last_session if parsed.plan.recommendations else None,
            }
        )
        now = self.clock.now()
        self.apply_ladder_overrides_service.execute(parsed.overrides, last_session, now)
        memory = self.coach_memory_repository.get()
        memory.apply(list(parsed.memory), by=f"coach-run:{run_id}", today=now.date())
        self.coach_memory_repository.update(memory)
        self.coach_plan_repository.update(plan)
        if last_session and plan.recommendations:  # the journal keeps every answer to the athlete's feedback
            earlier = self.coach_review_repository.get_by_id(last_session)
            review = earlier or CoachReview(session_id=last_session, written_at=iso(now))
            answered = review.with_recommendations(plan.recommendations)
            if earlier:
                self.coach_review_repository.update(answered)
            else:
                self.coach_review_repository.create(answered)
        return plan


class RunCoachService:
    """Asks the coach, validates its answer, stores the plan, and tells the athlete."""

    def __init__(
        self,
        coach: CoachProtocol,
        catalogue: Catalogue,
        prepare_coach_run_service: PrepareCoachRunService,
        log_coach_run_service: LogCoachRunService,
        store_coach_plan_service: StoreCoachPlanService,
        desktop: DesktopProtocol,
    ) -> None:
        self.coach = coach
        self.catalogue = catalogue
        self.prepare_coach_run_service = prepare_coach_run_service
        self.log_coach_run_service = log_coach_run_service
        self.store_coach_plan_service = store_coach_plan_service
        self.desktop = desktop

    def execute(self, command: RunCoachCommand) -> CoachPlan:
        """Raises CoachUnavailableError or CoachOutputError when no plan could be made."""
        request = self.prepare_coach_run_service.execute(command)
        try:
            output = self.coach.write_plan(request.context)
            parsed = parse_coach_output(output, self.catalogue, set(command.equipment), set(START))
        except (CoachUnavailableError, CoachOutputError) as error:
            self.log_coach_run_service.execute(request.started, str(error))
            raise
        plan = self.store_coach_plan_service.execute(
            parsed, request, self.log_coach_run_service.execute(request.started)
        )
        if plan.recommendations:
            self.desktop.notify(
                "Coach's feedback on your session", "\n".join(f"• {r.advice}" for r in plan.recommendations)
            )
        else:
            self.desktop.notify("Next session planned", plan.rationale or plan.hard.title)
        return plan

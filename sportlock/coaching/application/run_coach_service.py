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
    CoachRunLogProtocol,
    CoachUnavailableError,
)
from sportlock.coaching.domain.review import CoachReview
from sportlock.exercises.domain.catalogue import Catalogue
from sportlock.progression.domain.ladders import START, LadderMove, LadderPosition, LadderRepositoryProtocol, positions
from sportlock.progression.domain.rules import Proposal
from sportlock.shared_kernel.base import ValueObject
from sportlock.shared_kernel.ports import ClockProtocol, DesktopProtocol
from sportlock.shared_kernel.time import iso
from sportlock.training.domain.repositories import TrainingHistoryProtocol


class CoachRequest(ValueObject):
    """A prepared coach run: when it started, the data the plan is based on, and the coach's context."""

    started: datetime
    basis: str
    context: dict


class PrepareCoachRunService:
    """Notes the start and the plan's basis before the coach runs, since a session can end meanwhile."""

    def __init__(
        self, clock: ClockProtocol, freshness: CoachPlanFreshness, build_context: BuildCoachContextService
    ) -> None:
        self.clock = clock
        self.freshness = freshness
        self.build_context = build_context

    def execute(self, command: RunCoachCommand) -> CoachRequest:
        """The request to send to the coach."""
        started = self.clock.now()
        basis = self.freshness.basis()
        return CoachRequest(started=started, basis=basis, context=self.build_context.execute(started, command))


class LogCoachRunService:
    """Records each coach run, successful or not, with how long it took."""

    def __init__(self, runs: CoachRunLogProtocol, clock: ClockProtocol) -> None:
        self.runs = runs
        self.clock = clock

    def execute(self, started: datetime, error: str | None = None) -> int:
        """The run's id."""
        seconds = round((self.clock.now() - started).total_seconds())
        return self.runs.add(CoachRun(at=iso(started), seconds=seconds, ok=error is None, error=error))


class ApplyLadderOverridesService:
    """Moves the ladders the coach overrode, recording each move as the coach's decision."""

    def __init__(self, ladders: LadderRepositoryProtocol) -> None:
        self.ladders = ladders

    def execute(self, overrides: tuple[LadderOverride, ...], last_session: int | None, now: datetime) -> None:
        """Apply the overrides in order."""
        current = {p.chain: p for p in positions(self.ladders.saved())}
        for override in overrides:
            before = current[override.chain]
            self.ladders.mark_last_overridden(override.chain, override.reason)
            self.ladders.record_move(
                last_session or 0,
                LadderMove(
                    chain=override.chain,
                    session_exercise_id=0,
                    from_exercise=before.exercise,
                    from_target=before.target,
                    proposal=Proposal(
                        rule="override", exercise=override.exercise, target=override.target, reason=override.reason
                    ),
                ),
                decided_by="agent",
                now=now,
            )
            self.ladders.set(
                LadderPosition(
                    chain=override.chain,
                    exercise=override.exercise,
                    target=override.target,
                    reason=f"Coach: {override.reason}",
                ),
                now,
            )


class StoreCoachPlanService:
    """Stores what the coach wrote: ladder overrides, memory changes, the plan, and its answer to feedback."""

    def __init__(
        self,
        apply_overrides: ApplyLadderOverridesService,
        history: TrainingHistoryProtocol,
        memory: CoachMemoryRepositoryProtocol,
        plans: CoachPlanRepositoryProtocol,
        reviews: CoachReviewRepositoryProtocol,
        clock: ClockProtocol,
    ) -> None:
        self.apply_overrides = apply_overrides
        self.history = history
        self.memory = memory
        self.plans = plans
        self.reviews = reviews
        self.clock = clock

    def execute(self, parsed: ParsedCoachOutput, request: CoachRequest, run_id: int) -> CoachPlan:
        """The stored plan."""
        last_session = self.history.last_counted_id()
        plan = parsed.plan.model_copy(
            update={
                "basis": request.basis,
                "generated_at": iso(request.started),
                "feedback_session": last_session if parsed.plan.recommendations else None,
            }
        )
        now = self.clock.now()
        self.apply_overrides.execute(parsed.overrides, last_session, now)
        memory = self.memory.load()
        memory.apply(list(parsed.memory), by=f"coach-run:{run_id}", today=now.date())
        self.memory.save(memory)
        self.plans.save(plan)
        if last_session and plan.recommendations:  # the journal keeps every answer to the athlete's feedback
            review = self.reviews.get(last_session) or CoachReview(session_id=last_session, written_at=iso(now))
            self.reviews.save(review.with_recommendations(plan.recommendations))
        return plan


class RunCoachService:
    """Asks the coach, validates its answer, stores the plan, and tells the athlete."""

    def __init__(
        self,
        coach: CoachProtocol,
        catalogue: Catalogue,
        prepare: PrepareCoachRunService,
        log_run: LogCoachRunService,
        store_plan: StoreCoachPlanService,
        desktop: DesktopProtocol,
    ) -> None:
        self.coach = coach
        self.catalogue = catalogue
        self.prepare = prepare
        self.log_run = log_run
        self.store_plan = store_plan
        self.desktop = desktop

    def execute(self, command: RunCoachCommand) -> CoachPlan:
        """Raises CoachUnavailableError or CoachOutputError when no plan could be made."""
        request = self.prepare.execute(command)
        try:
            output = self.coach.write_plan(request.context)
            parsed = parse_coach_output(output, self.catalogue, set(command.equipment), set(START))
        except (CoachUnavailableError, CoachOutputError) as error:
            self.log_run.execute(request.started, str(error))
            raise
        plan = self.store_plan.execute(parsed, request, self.log_run.execute(request.started))
        if plan.recommendations:
            self.desktop.notify(
                "Coach's feedback on your session", "\n".join(f"• {r.advice}" for r in plan.recommendations)
            )
        else:
            self.desktop.notify("Next session planned", plan.rationale or plan.hard.title)
        return plan

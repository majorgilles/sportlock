"""Plan the next session with the coach. Runs after every session and every profile change, in the
background: it can take minutes."""

from __future__ import annotations

from datetime import datetime, timedelta

from sportlock.athlete.domain.profile import ProfileRepositoryProtocol
from sportlock.coaching.application.fresh_plan import CoachPlanFreshness
from sportlock.coaching.domain.plan import CoachOutputError, CoachPlan, parse_coach_output
from sportlock.coaching.domain.ports import (
    CoachMemoryRepositoryProtocol,
    CoachPlanRepositoryProtocol,
    CoachProtocol,
    CoachRun,
    CoachRunLogProtocol,
    CoachUnavailableError,
)
from sportlock.exercises.domain.catalogue import Catalogue
from sportlock.locks.domain.repositories import LockEventRepositoryProtocol
from sportlock.progression.domain.ladders import START, LadderMove, LadderPosition, LadderRepositoryProtocol, positions
from sportlock.progression.domain.rules import Proposal
from sportlock.recovery.domain.policy import load_summary, rest_days_in_a_row
from sportlock.shared_kernel.base import ValueObject
from sportlock.shared_kernel.ports import ClockProtocol, DesktopProtocol
from sportlock.shared_kernel.time import iso
from sportlock.training.domain.repositories import TrainingHistoryProtocol

HISTORY_DAYS = 28
HISTORY_SESSIONS = 12
FORGOTTEN_SHOWN = 30


class RunCoachCommand(ValueObject):
    """Plan the next session. Handled by `RunCoachService`."""

    equipment: frozenset[str]
    upcoming_locks: tuple[dict, ...] = ()  # [{"start", "minutes"}] of the next scheduled locks
    rest_policy: dict = {}


class RunCoachService:
    """Asks the coach, validates, then stores the plan, its ladder overrides and memory changes."""

    def __init__(
        self,
        coach: CoachProtocol,
        catalogue: Catalogue,
        plans: CoachPlanRepositoryProtocol,
        runs: CoachRunLogProtocol,
        memory: CoachMemoryRepositoryProtocol,
        ladders: LadderRepositoryProtocol,
        history: TrainingHistoryProtocol,
        profiles: ProfileRepositoryProtocol,
        lock_events: LockEventRepositoryProtocol,
        desktop: DesktopProtocol,
        clock: ClockProtocol,
    ) -> None:
        self.coach = coach
        self.catalogue = catalogue
        self.plans = plans
        self.runs = runs
        self.memory = memory
        self.ladders = ladders
        self.history = history
        self.profiles = profiles
        self.lock_events = lock_events
        self.desktop = desktop
        self.clock = clock
        self.freshness = CoachPlanFreshness(plans, history, profiles)

    def execute(self, command: RunCoachCommand) -> CoachPlan:
        """Raises CoachUnavailableError or CoachOutputError when no plan could be made."""
        started = self.clock.now()
        basis = self.freshness.basis()
        try:
            output = self.coach.write_plan(self.context(started, command))
            parsed = parse_coach_output(output, self.catalogue, set(command.equipment), set(START))
        except (CoachUnavailableError, CoachOutputError) as error:
            self.runs.add(CoachRun(at=iso(started), seconds=self._seconds(started), ok=False, error=str(error)))
            raise
        run_id = self.runs.add(CoachRun(at=iso(started), seconds=self._seconds(started), ok=True))

        last_session = self.history.last_counted_id()
        plan = parsed.plan.model_copy(
            update={
                "basis": basis,
                "generated_at": iso(started),
                "feedback_session": last_session if parsed.plan.recommendations else None,
            }
        )
        now = self.clock.now()
        current = {p.chain: p for p in positions(self.ladders.saved())}
        for override in parsed.overrides:
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
        memory = self.memory.load()
        memory.apply(list(parsed.memory), by=f"coach-run:{run_id}", today=now.date())
        self.memory.save(memory)
        self.plans.save(plan)

        if plan.recommendations:
            self.desktop.notify(
                "Coach's feedback on your session", "\n".join(f"• {r.advice}" for r in plan.recommendations)
            )
        else:
            self.desktop.notify("Next session planned", plan.rationale or plan.hard.title)
        return plan

    def _seconds(self, started: datetime) -> int:
        return round((self.clock.now() - started).total_seconds())

    def context(self, now: datetime, command: RunCoachCommand) -> dict:
        """Everything the coach sees."""
        profile = self.profiles.get()
        ladders = []
        for position in positions(self.ladders.saved()):
            exercise = self.catalogue.get(position.exercise)
            ladders.append(
                {
                    "chain": position.chain,
                    "exercise": position.exercise,
                    "name": exercise.name,
                    "step": f"{exercise.step}/{len(exercise.chain_ids)}",
                    "target": position.target.to_dict(),
                    "why": position.reason,
                }
            )
        recent = self.history.recent_details((now - timedelta(days=HISTORY_DAYS)).date(), HISTORY_SESSIONS)
        proposals = []
        if recent:
            proposals = self.ladders.moves_for(self.history.last_counted_id() or 0)
        week = self.history.credited_since(now - timedelta(days=7))
        catalogue = [
            {
                "id": e.id,
                "name": e.name,
                "chain": e.chain,
                "step": e.step,
                "kind": e.kind,
                **({"sides": e.sides} if e.sides else {}),
                "equipment": sorted(e.equipment),
                "available": e.available_with(command.equipment),
            }
            for e in self.catalogue.all()
        ]
        memory = self.memory.load()
        last_hard = self.history.last_hard()
        return {
            "now": iso(now),
            "profile": {k: v for k, v in profile.to_dict().items() if k != "updated_at"} if profile else {},
            "coach_memory": [
                {"id": n.id, "topic": n.topic, "note": n.text, "since": n.since.isoformat()} for n in memory.current()
            ],
            "forgotten_by_user": self.memory.forgotten(FORGOTTEN_SHOWN),
            "equipment": sorted(command.equipment),
            "last_hard_session": iso(last_hard) if last_hard else None,
            "ladders": ladders,
            "rule_proposals_from_last_session": proposals,
            "load": load_summary(week, rest_days_in_a_row(self.lock_events.rest_days(), now.date()), now),
            "upcoming_locks": list(command.upcoming_locks),
            "rest_policy": command.rest_policy,
            "recent_sessions": recent,
            "catalogue": catalogue,
        }

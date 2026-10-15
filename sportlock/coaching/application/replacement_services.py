"""The exercises replaced after the last session, and the athlete's answer.

Agree, or keep the old exercise (the warning popup and the lock screen ask before the session).
"""

from __future__ import annotations

from datetime import datetime

from sportlock.coaching.application.fresh_plan import CoachPlanFreshness
from sportlock.coaching.domain.ports import (
    CoachPlanRepositoryProtocol,
    ReplacementAnswerFilters,
    ReplacementAnswerRepositoryProtocol,
)
from sportlock.coaching.domain.replacements import Replacement, ReplacementAnswer, ReplacementError, replacements_after
from sportlock.exercises.domain.catalogue import Catalogue
from sportlock.progression.domain.ladders import (
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
from sportlock.shared_kernel.time import iso
from sportlock.training.application.training_history import TrainingHistory
from sportlock.training.domain.repositories import TrainingSessionRepositoryProtocol


class PendingReplacementsService:
    """The replacements after the last counted session that the athlete hasn't answered yet."""

    def __init__(
        self,
        training_history: TrainingHistory,
        ladder_position_repository: LadderPositionRepositoryProtocol,
        ladder_move_record_repository: LadderMoveRecordRepositoryProtocol,
        replacement_answer_repository: ReplacementAnswerRepositoryProtocol,
        catalogue: Catalogue,
    ) -> None:
        self.training_history = training_history
        self.ladder_position_repository = ladder_position_repository
        self.ladder_move_record_repository = ladder_move_record_repository
        self.replacement_answer_repository = replacement_answer_repository
        self.catalogue = catalogue

    def execute(self) -> list[Replacement]:
        """Oldest chain first; [] before the first session."""
        session_id = self.training_history.last_counted_id()
        if session_id is None:
            return []
        current = {
            p.chain: p.exercise for p in positions(self.ladder_position_repository.find(LadderPositionFilters()))
        }
        answered = {
            answer.chain
            for answer in self.replacement_answer_repository.find(ReplacementAnswerFilters(session_id=session_id))
        }
        found = replacements_after(
            session_id,
            [
                record.to_dict()
                for record in self.ladder_move_record_repository.find(LadderMoveRecordFilters(session_id=session_id))
            ],
            current,
            self.catalogue,
        )
        return [r for r in found if r.chain not in answered]


class AnswerReplacementCommand(ValueObject):
    """The athlete's answer to the replacement on one chain. Handled by `AnswerReplacementService`."""

    chain: str
    agree: bool
    note: str = ""


class KeepReplacedExerciseService:
    """Puts a replaced exercise back: on the ladder, in the coach's plan, and in a session that hasn't reached it."""

    def __init__(
        self,
        ladder_position_repository: LadderPositionRepositoryProtocol,
        ladder_move_record_repository: LadderMoveRecordRepositoryProtocol,
        coach_plan_repository: CoachPlanRepositoryProtocol,
        coach_plan_freshness: CoachPlanFreshness,
        training_session_repository: TrainingSessionRepositoryProtocol,
        catalogue: Catalogue,
    ) -> None:
        self.ladder_position_repository = ladder_position_repository
        self.ladder_move_record_repository = ladder_move_record_repository
        self.coach_plan_repository = coach_plan_repository
        self.coach_plan_freshness = coach_plan_freshness
        self.training_session_repository = training_session_repository
        self.catalogue = catalogue

    def execute(self, replacement: Replacement, answer: ReplacementAnswer, now: datetime) -> None:
        """Keep the old exercise, recorded as the athlete's decision."""
        reason = f"You kept {replacement.from_name} instead of {replacement.to_name}" + (
            f": {answer.note}" if answer.note else "."
        )
        target = replacement.from_target.with_(progress=None)
        current = {p.chain: p for p in positions(self.ladder_position_repository.find(LadderPositionFilters()))}[
            replacement.chain
        ]
        self.ladder_move_record_repository.create(
            LadderMoveRecord(
                session_id=replacement.session_id,
                move=LadderMove(
                    chain=replacement.chain,
                    session_exercise_id=0,
                    from_exercise=current.exercise,
                    from_target=current.target,
                    proposal=Proposal(rule="kept", exercise=replacement.from_exercise, target=target, reason=reason),
                ),
                decided_by="athlete",
                decided_at=now,
            )
        )
        self.ladder_position_repository.update(
            LadderPosition(
                chain=replacement.chain,
                exercise=replacement.from_exercise,
                target=target,
                reason=reason,
                updated_at=now,
            )
        )
        plan = self.coach_plan_freshness.fresh_plan()
        if plan is not None:  # still fresh afterwards: its basis doesn't change
            self.coach_plan_repository.update(
                plan.keeping(replacement.to_exercise, replacement.from_exercise, target, reason)
            )
        session = self.training_session_repository.get()
        if session is not None and session.keep_instead(
            replacement.to_exercise, replacement.from_exercise, target.with_(progress=reason), self.catalogue
        ):
            self.training_session_repository.update(session)


class AnswerReplacementService:
    """Records the athlete's answer; keeping the old exercise puts it back everywhere."""

    def __init__(
        self,
        pending_replacements_service: PendingReplacementsService,
        replacement_answer_repository: ReplacementAnswerRepositoryProtocol,
        keep_replaced_exercise_service: KeepReplacedExerciseService,
    ) -> None:
        self.pending_replacements_service = pending_replacements_service
        self.replacement_answer_repository = replacement_answer_repository
        self.keep_replaced_exercise_service = keep_replaced_exercise_service

    def execute(self, command: AnswerReplacementCommand, now: datetime) -> Replacement:
        """The replacement answered. Raises ReplacementError when there is none on that chain."""
        replacement = next((r for r in self.pending_replacements_service.execute() if r.chain == command.chain), None)
        if replacement is None:
            raise ReplacementError("no replaced exercise to answer on that chain")
        answer = ReplacementAnswer.to(replacement, agreed=command.agree, note=command.note, answered_at=iso(now))
        self.replacement_answer_repository.create(answer)
        if not command.agree:
            self.keep_replaced_exercise_service.execute(replacement, answer, now)
        return replacement

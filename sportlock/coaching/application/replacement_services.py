"""The exercises replaced after the last session, and the athlete's answer: agree, or keep the old
exercise (the warning popup and the lock screen ask before the session)."""

from __future__ import annotations

from datetime import datetime

from sportlock.coaching.application.fresh_plan import CoachPlanFreshness
from sportlock.coaching.domain.ports import CoachPlanRepositoryProtocol, ReplacementAnswerRepositoryProtocol
from sportlock.coaching.domain.replacements import Replacement, ReplacementAnswer, ReplacementError, replacements_after
from sportlock.exercises.domain.catalogue import Catalogue
from sportlock.progression.domain.ladders import LadderMove, LadderPosition, LadderRepositoryProtocol, positions
from sportlock.progression.domain.rules import Proposal
from sportlock.shared_kernel.base import ValueObject
from sportlock.shared_kernel.time import iso
from sportlock.training.domain.repositories import TrainingHistoryProtocol, TrainingSessionRepositoryProtocol


class PendingReplacementsService:
    """The replacements after the last counted session that the athlete hasn't answered yet."""

    def __init__(
        self,
        history: TrainingHistoryProtocol,
        ladders: LadderRepositoryProtocol,
        answers: ReplacementAnswerRepositoryProtocol,
        catalogue: Catalogue,
    ) -> None:
        self.history = history
        self.ladders = ladders
        self.answers = answers
        self.catalogue = catalogue

    def execute(self) -> list[Replacement]:
        """Oldest chain first; [] before the first session."""
        session_id = self.history.last_counted_id()
        if session_id is None:
            return []
        current = {p.chain: p.exercise for p in positions(self.ladders.saved())}
        answered = self.answers.answered(session_id)
        found = replacements_after(session_id, self.ladders.moves_for(session_id), current, self.catalogue)
        return [r for r in found if r.chain not in answered]


class AnswerReplacementCommand(ValueObject):
    """The athlete's answer to the replacement on one chain. Handled by `AnswerReplacementService`."""

    chain: str
    agree: bool
    note: str = ""


class AnswerReplacementService:
    """Records the answer. Keeping the old exercise moves the ladder back and puts the exercise back
    in the coach's plan and in a session that hasn't reached it yet."""

    def __init__(
        self,
        pending: PendingReplacementsService,
        answers: ReplacementAnswerRepositoryProtocol,
        ladders: LadderRepositoryProtocol,
        plans: CoachPlanRepositoryProtocol,
        freshness: CoachPlanFreshness,
        sessions: TrainingSessionRepositoryProtocol,
        catalogue: Catalogue,
    ) -> None:
        self.pending = pending
        self.answers = answers
        self.ladders = ladders
        self.plans = plans
        self.freshness = freshness
        self.sessions = sessions
        self.catalogue = catalogue

    def execute(self, command: AnswerReplacementCommand, now: datetime) -> Replacement:
        """The replacement answered. Raises ReplacementError when there is none on that chain."""
        replacement = next((r for r in self.pending.execute() if r.chain == command.chain), None)
        if replacement is None:
            raise ReplacementError("no replaced exercise to answer on that chain")
        answer = ReplacementAnswer.to(replacement, agreed=command.agree, note=command.note, answered_at=iso(now))
        self.answers.add(answer)
        if not command.agree:
            self._keep(replacement, answer, now)
        return replacement

    def _keep(self, replacement: Replacement, answer: ReplacementAnswer, now: datetime) -> None:
        reason = f"You kept {replacement.from_name} instead of {replacement.to_name}" + (
            f": {answer.note}" if answer.note else "."
        )
        target = replacement.from_target.with_(progress=None)
        current = {p.chain: p for p in positions(self.ladders.saved())}[replacement.chain]
        self.ladders.record_move(
            replacement.session_id,
            LadderMove(
                chain=replacement.chain,
                session_exercise_id=0,
                from_exercise=current.exercise,
                from_target=current.target,
                proposal=Proposal(rule="kept", exercise=replacement.from_exercise, target=target, reason=reason),
            ),
            decided_by="athlete",
            now=now,
        )
        self.ladders.set(
            LadderPosition(chain=replacement.chain, exercise=replacement.from_exercise, target=target, reason=reason),
            now,
        )
        plan = self.freshness.fresh_plan()
        if plan is not None:  # still fresh afterwards: its basis doesn't change
            self.plans.save(plan.keeping(replacement.to_exercise, replacement.from_exercise, target, reason))
        session = self.sessions.active()
        if session is not None and session.keep_instead(
            replacement.to_exercise, replacement.from_exercise, target.with_(progress=reason), self.catalogue
        ):
            self.sessions.save(session)

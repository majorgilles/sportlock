"""Move the ladders from what a closed session actually did (test sessions never count)."""

from __future__ import annotations

from datetime import datetime

from sportlock.exercises.domain.catalogue import Catalogue
from sportlock.progression.domain.ladders import (
    ExerciseResult,
    LadderMove,
    LadderPosition,
    LadderRepositoryProtocol,
    moves_for_session,
)
from sportlock.progression.domain.rules import SetResult
from sportlock.training.domain.session import TrainingSession


class ApplySessionProgressionService:
    """Runs the rules over the session and moves the chains."""

    def __init__(self, ladders: LadderRepositoryProtocol, catalogue: Catalogue) -> None:
        self.ladders = ladders
        self.catalogue = catalogue

    def execute(self, session: TrainingSession, now: datetime) -> list[LadderMove]:
        """The applied moves, in order."""
        if session.kind == "test" or session.id is None:
            return []
        results = [ExerciseResult(session_exercise_id=e.id or 0, exercise=e.exercise, name=e.name, kind=e.kind,
                                  target=e.target, status=e.status, rpe=e.rpe,
                                  sets=tuple(SetResult(reps=s.reps, seconds=s.seconds) for s in e.sets))
                   for e in sorted(session.exercises, key=lambda e: e.id or 0) if e.status in ("done", "swapped")]
        moves = moves_for_session(results, self.catalogue)
        for move in moves:
            self.ladders.record_move(session.id, move, decided_by="rules", now=now)
            self.ladders.set(LadderPosition(chain=move.chain, exercise=move.proposal.exercise,
                                            target=move.proposal.target, reason=move.proposal.reason), now)
        return moves

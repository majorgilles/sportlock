"""Progression ladders: one position per chain, where each starts, and how a session moves them."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from sportlock.exercises.domain.catalogue import Catalogue
from sportlock.progression.domain.rules import DoneExercise, Proposal, SetResult, propose, too_hard
from sportlock.shared_kernel.base import ValueObject
from sportlock.shared_kernel.repositories import (
    CreateRepositoryProtocol,
    DeleteRepositoryProtocol,
    FindRepositoryProtocol,
    UpdateRepositoryProtocol,
)
from sportlock.shared_kernel.targets import Target


def _t(sets: int, rest: int, reps: tuple[int, int] | None = None, seconds: int | None = None) -> Target:
    return Target(sets=sets, rest=rest, reps=reps, seconds=seconds)


# Where a beginner starts on each laddered chain. Chains not listed here (warm-up, mobility,
# conditioning) are menus, not ladders.
START: dict[str, tuple[str, Target]] = {
    "push-horizontal": ("incline-push-up", _t(3, 60, (8, 12))),
    "push-vertical": ("pike-push-up", _t(3, 90, (5, 8))),
    "dip": ("bench-dip", _t(3, 60, (8, 12))),
    "pull-horizontal": ("prone-y-raise", _t(3, 45, (10, 12))),
    "pull-vertical": ("scapular-pull-up", _t(3, 90, (6, 10))),
    "squat": ("bodyweight-squat", _t(3, 60, (12, 15))),
    "hinge": ("glute-bridge", _t(3, 45, (12, 15))),
    "core": ("plank", _t(3, 45, seconds=30)),
    "core-flexion": ("crunch", _t(3, 45, (12, 15))),
    "calf": ("calf-raise", _t(3, 45, (15, 20))),
    "hip": ("standing-hip-abduction", _t(2, 30, (12, 15))),
}

# Starting positions above beginner level, set once when the profile is first saved.
STARTING_POINTS: dict[str, dict[str, tuple[str, Target]]] = {
    "intermediate": {
        "push-horizontal": ("push-up", _t(3, 60, (8, 12))),
        "push-vertical": ("pike-push-up", _t(3, 90, (6, 10))),
        "dip": ("bench-dip", _t(3, 60, (10, 15))),
        "pull-horizontal": ("table-inverted-row", _t(3, 60, (8, 12))),
        "pull-vertical": ("negative-pull-up", _t(3, 120, (3, 5))),
        "squat": ("split-squat", _t(3, 60, (8, 12))),
        "hinge": ("single-leg-glute-bridge", _t(3, 45, (8, 12))),
        "core": ("plank", _t(3, 45, seconds=45)),
        "core-flexion": ("sit-up", _t(3, 60, (10, 15))),
        "calf": ("single-leg-calf-raise", _t(3, 45, (10, 15))),
        "hip": ("side-lying-leg-raise", _t(2, 30, (15, 20))),
    },
    "advanced": {
        "push-horizontal": ("diamond-push-up", _t(4, 90, (8, 12))),
        "push-vertical": ("elevated-pike-push-up", _t(4, 120, (6, 10))),
        "dip": ("parallel-bar-dip", _t(4, 120, (6, 10))),
        "pull-horizontal": ("feet-elevated-inverted-row", _t(4, 90, (8, 12))),
        "pull-vertical": ("pull-up", _t(4, 150, (5, 8))),
        "squat": ("bulgarian-split-squat", _t(4, 90, (8, 12))),
        "hinge": ("hamstring-walkout", _t(3, 90, (6, 10))),
        "core": ("hollow-body-hold", _t(3, 60, seconds=40)),
        "core-flexion": ("v-up", _t(3, 60, (8, 12))),
        "calf": ("elevated-single-leg-calf-raise", _t(3, 45, (12, 15))),
        "hip": ("donkey-kick", _t(3, 30, (12, 15))),
    },
}


def laddered(chain: str) -> bool:
    """Whether a chain is a progression ladder (rules move it) rather than a menu."""
    return chain in START


class LadderPosition(ValueObject):
    """Where the athlete is on one chain, the target for next time, and why."""

    chain: str
    exercise: str
    target: Target
    reason: str | None = None
    updated_at: datetime | None = None

    @classmethod
    def start(cls, chain: str) -> LadderPosition:
        """The beginner starting point of a chain."""
        exercise, target = START[chain]
        return cls(chain=chain, exercise=exercise, target=target)


class ExerciseResult(ValueObject):
    """What happened to one exercise of a session, as the rules need it."""

    session_exercise_id: int
    exercise: str
    name: str
    kind: str
    target: Target
    status: str  # done | swapped (other statuses don't move ladders)
    rpe: int | None
    sets: tuple[SetResult, ...]


class LadderMove(ValueObject):
    """A proposal applied to a chain, with what it replaced."""

    chain: str
    session_exercise_id: int
    from_exercise: str
    from_target: Target
    proposal: Proposal


def moves_for_session(results: list[ExerciseResult], catalogue: Catalogue) -> list[LadderMove]:
    """Run the rules over every done or swapped exercise, in order (later ones win on a chain)."""
    moves = []
    for result in results:
        exercise = catalogue.find(result.exercise)
        if exercise is None or not laddered(exercise.chain):
            continue  # warm-up, mobility and cool-down blocks are not laddered
        easier = (exercise.easier, catalogue.name(exercise.easier)) if exercise.easier else None
        harder = (exercise.harder, catalogue.name(exercise.harder)) if exercise.harder else None
        if result.status == "swapped":
            proposal = too_hard(name=result.name, target=result.target, easier=easier) if easier else None
        elif result.status == "done":
            done = DoneExercise(
                exercise=result.exercise,
                kind=result.kind,
                target=result.target,
                sets=tuple(result.sets),
                rpe=result.rpe,
            )
            proposal = propose(done, easier, harder)
        else:
            proposal = None
        if proposal is not None:
            new_kind = catalogue.get(proposal.exercise).kind
            if new_kind != exercise.kind:  # chains mix reps and holds: express the target for the new exercise
                proposal = proposal.model_copy(update={"target": proposal.target.for_kind(new_kind)})
            moves.append(
                LadderMove(
                    chain=exercise.chain,
                    session_exercise_id=result.session_exercise_id,
                    from_exercise=result.exercise,
                    from_target=result.target,
                    proposal=proposal,
                )
            )
    return moves


class LadderMoveRecord(ValueObject):
    """A ladder move as it was kept: the session it followed, who decided it, and whether the coach overrode it.

    `id` is set once stored.
    """

    session_id: int
    move: LadderMove
    decided_by: str  # rules | agent | athlete
    decided_at: datetime
    status: str = "applied"  # applied | overridden
    override_reason: str | None = None
    id: int | None = None

    def overridden(self, reason: str) -> LadderMoveRecord:
        """The same move, marked as one the coach disagreed with."""
        return self.model_copy(update={"status": "overridden", "override_reason": reason})

    def to_dict(self) -> dict:
        """The shape the coach and the report read: {chain, rule, from, from_target, to, target, reason, decided_by}."""
        return {
            "chain": self.move.chain,
            "rule": self.move.proposal.rule,
            "from": self.move.from_exercise,
            "from_target": self.move.from_target.to_dict(),
            "to": self.move.proposal.exercise,
            "target": self.move.proposal.target.to_dict(),
            "reason": self.move.proposal.reason,
            "decided_by": self.decided_by,
        }


class LadderPositionFilters(ValueObject):
    """Which saved positions to load: every chain moved away from its start."""


class LadderMoveRecordFilters(ValueObject):
    """Which kept moves to load: by session, chain and status, and the latest `latest` of them; oldest first."""

    session_id: int | None = None
    chain: str | None = None
    status: str | None = None
    latest: int | None = None


class LadderPositionRepositoryProtocol(
    FindRepositoryProtocol[LadderPosition, LadderPositionFilters],
    UpdateRepositoryProtocol[LadderPosition],
    DeleteRepositoryProtocol[str],
    Protocol,
):
    """Ladder positions moved away from their start, by chain; `delete` puts a chain back at its start."""


class LadderMoveRecordRepositoryProtocol(
    CreateRepositoryProtocol[LadderMoveRecord],
    FindRepositoryProtocol[LadderMoveRecord, LadderMoveRecordFilters],
    UpdateRepositoryProtocol[LadderMoveRecord],
    Protocol,
):
    """Every ladder move kept, in order."""


def positions(saved: list[LadderPosition]) -> list[LadderPosition]:
    """Every laddered chain's position, falling back to its start."""
    by_chain = {position.chain: position for position in saved}
    return [by_chain.get(chain) or LadderPosition.start(chain) for chain in START]

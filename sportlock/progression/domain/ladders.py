"""Progression ladders: one position per chain, where each starts, and how a session moves them."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from sportlock.exercises.domain.catalogue import Catalogue
from sportlock.progression.domain.rules import Proposal, SetResult, propose, too_hard
from sportlock.shared_kernel.base import ValueObject
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
            proposal = propose(exercise=result.exercise, name=result.name, kind=result.kind, target=result.target,
                               sets=list(result.sets), rpe=result.rpe, easier=easier, harder=harder)
        else:
            proposal = None
        if proposal is not None:
            new_kind = catalogue.get(proposal.exercise).kind
            if new_kind != exercise.kind:  # chains mix reps and holds: express the target for the new exercise
                proposal = proposal.model_copy(update={"target": proposal.target.for_kind(new_kind)})
            moves.append(LadderMove(chain=exercise.chain, session_exercise_id=result.session_exercise_id,
                                    from_exercise=result.exercise, from_target=result.target, proposal=proposal))
    return moves


class LadderRepositoryProtocol(Protocol):
    """Ladder positions and the history of moves."""

    def saved(self) -> dict[str, LadderPosition]:
        """Positions moved away from their start, by chain."""
        ...

    def set(self, position: LadderPosition, now: datetime) -> None:
        """Move a chain."""
        ...

    def reset(self, chain: str) -> None:
        """Back to the starting point."""
        ...

    def record_move(self, session_id: int, move: LadderMove, *, decided_by: str, now: datetime) -> None:
        """Keep the proposal that moved a chain (decided_by: rules | agent)."""
        ...

    def mark_last_overridden(self, chain: str, reason: str) -> None:
        """The coach disagreed with the rules' last move on this chain."""
        ...

    def moves_for(self, session_id: int) -> list[dict]:
        """Moves recorded for a session, as {chain, rule, from, to, target, reason}."""
        ...


def positions(saved: dict[str, LadderPosition]) -> list[LadderPosition]:
    """Every laddered chain's position, falling back to its start."""
    return [saved.get(chain) or LadderPosition.start(chain) for chain in START]

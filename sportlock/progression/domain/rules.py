"""Difficulty rules: from one exercise's result, propose the next target on its ladder.

Reps exercises (double progression, then move along the chain):
  - every set at the top of the range, effort ≤ 6      → up: harder variation (same sets/range)
  - every set at the top of the range, effort 7–8      → add: range +1 rep; at a top of 20, up
  - every set at the top of the range, effort 9–10     → hold: repeat
  - a set below the range, or fewer sets than planned  → down: easier variation
  - effort 9–10 without every set at the top           → down: easier variation
  - otherwise (inside the range)                       → hold: aim for more reps
Holds work the same in seconds: +10 s when easy, +5 s when moderate, and from 60 s an easy
result moves to the harder variation at 20 s. "Too hard" during a session is always down.
Timed blocks (warm-up, cool-down) never change.
"""

from __future__ import annotations

from typing import Literal

from sportlock.shared_kernel.base import ValueObject
from sportlock.shared_kernel.targets import Target

type Rule = Literal["up", "add", "hold", "down", "too-hard", "override"]
type Neighbour = tuple[str, str]  # (exercise id, name)

MAX_TOP_REPS = 20
HOLD_UP_AT = 60
HOLD_RESTART = 20
HOLD_MAX = 90


class Proposal(ValueObject):
    """Where a ladder should go next and why."""

    rule: Rule
    exercise: str  # exercise id for next time
    target: Target
    reason: str


class SetResult(ValueObject):
    """One logged set: reps for reps exercises, seconds (per side) for all."""

    reps: int | None
    seconds: float


def propose(
    *,
    exercise: str,
    name: str,
    kind: str,
    target: Target,
    sets: list[SetResult],
    rpe: int | None,
    easier: Neighbour | None,
    harder: Neighbour | None,
) -> Proposal | None:
    """The rule's proposal for one done exercise, or None when no rule applies."""
    if kind == "timed" or rpe is None or not sets:
        return None
    target = target.with_(progress=None, sides=None)
    if kind == "reps":
        return _reps(exercise, target, [s.reps or 0 for s in sets], rpe, easier, harder)
    return _hold(exercise, target, [s.seconds for s in sets], rpe, easier, harder)


def too_hard(*, name: str, target: Target, easier: Neighbour) -> Proposal:
    """ "Too hard" was pressed during the session: down to the easier variation."""
    return Proposal(
        rule="too-hard",
        exercise=easier[0],
        target=target.with_(progress=None, sides=None),
        reason=f"{name} was too hard → {easier[1]}",
    )


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _reps(
    exercise: str, target: Target, reps: list[int], rpe: int, easier: Neighbour | None, harder: Neighbour | None
) -> Proposal:
    assert target.reps is not None
    lo, hi = target.reps
    planned, done = target.sets, len(reps)
    all_top = done >= planned and all(r >= hi for r in reps)
    missed = done < planned or any(r < lo for r in reps)
    did = f"{' / '.join(map(str, reps))} reps at effort {rpe}"

    if all_top and rpe <= 6 or all_top and rpe <= 8 and hi >= MAX_TOP_REPS:
        if harder:
            return Proposal(
                rule="up",
                exercise=harder[0],
                target=target.with_(reps=(lo, hi) if hi < MAX_TOP_REPS else (8, 12)),
                reason=f"{did}: top of the range → {harder[1]}",
            )
        return Proposal(
            rule="add",
            exercise=exercise,
            target=target.with_(reps=(lo + 2, hi + 2)),
            reason=f"{did}: hardest variation, +2 reps",
        )
    if all_top and rpe <= 8:
        return Proposal(
            rule="add", exercise=exercise, target=target.with_(reps=(lo + 1, hi + 1)), reason=f"{did}: +1 rep per set"
        )
    if missed or rpe >= 9 and not all_top:
        why = f"{_plural(done, 'set')} of {planned}" if done < planned else did
        if easier:
            return Proposal(rule="down", exercise=easier[0], target=target, reason=f"{why}: too hard → {easier[1]}")
        return Proposal(
            rule="down",
            exercise=exercise,
            target=target.with_(reps=(max(3, lo - 2), max(5, hi - 2))),
            reason=f"{why}: easiest variation, −2 reps",
        )
    return Proposal(
        rule="hold", exercise=exercise, target=target, reason=f"{did}: same again, aim for {hi} on every set"
    )


def _hold(
    exercise: str, target: Target, seconds: list[float], rpe: int, easier: Neighbour | None, harder: Neighbour | None
) -> Proposal:
    goal = target.seconds or 0
    planned, done = target.sets, len(seconds)
    all_top = done >= planned and all(s >= goal for s in seconds)
    missed = done < planned or any(s < goal * 0.8 for s in seconds)
    did = f"{' / '.join(str(round(s)) for s in seconds)} s at effort {rpe}"

    if all_top and rpe <= 6:
        if goal >= HOLD_UP_AT and harder:
            return Proposal(
                rule="up",
                exercise=harder[0],
                target=target.with_(seconds=HOLD_RESTART),
                reason=f"{did}: {goal} s held → {harder[1]}",
            )
        return Proposal(
            rule="add", exercise=exercise, target=target.with_(seconds=min(HOLD_MAX, goal + 10)), reason=f"{did}: +10 s"
        )
    if all_top and rpe <= 8:
        return Proposal(
            rule="add", exercise=exercise, target=target.with_(seconds=min(HOLD_MAX, goal + 5)), reason=f"{did}: +5 s"
        )
    if missed or rpe >= 9 and not all_top:
        why = f"{_plural(done, 'set')} of {planned}" if done < planned else did
        if easier:
            return Proposal(
                rule="down",
                exercise=easier[0],
                target=target.with_(seconds=max(15, goal - 10)),
                reason=f"{why}: too hard → {easier[1]}",
            )
        return Proposal(
            rule="down", exercise=exercise, target=target.with_(seconds=max(10, goal - 10)), reason=f"{why}: −10 s"
        )
    return Proposal(rule="hold", exercise=exercise, target=target, reason=f"{did}: same again")

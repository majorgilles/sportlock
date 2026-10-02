"""Difficulty rules: from one exercise's result, propose the next target on its ladder.

Pure functions. A target is {"sets": n, "reps": [lo, hi] | "seconds": s, "rest": r}.

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

from dataclasses import dataclass

MAX_TOP_REPS = 20
HOLD_UP_AT = 60
HOLD_RESTART = 20
HOLD_MAX = 90


@dataclass(frozen=True)
class Proposal:
    rule: str  # up | add | hold | down | too-hard
    exercise: str  # exercise id for next time
    target: dict
    reason: str


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def propose(*, exercise: str, name: str, kind: str, target: dict, sets: list[dict], rpe: int | None,
            easier: tuple[str, str] | None, harder: tuple[str, str] | None) -> Proposal | None:
    """`sets`: [{"reps": int|None, "seconds": float}]; `easier`/`harder`: (id, name) or None."""
    if kind == "timed" or rpe is None or not sets:
        return None
    planned = target["sets"]
    done = len(sets)
    if kind == "reps":
        return _reps(exercise, name, target, [s["reps"] or 0 for s in sets], planned, done, rpe, easier, harder)
    return _hold(exercise, name, target, [s["seconds"] for s in sets], planned, done, rpe, easier, harder)


def too_hard(*, exercise: str, name: str, target: dict, easier: tuple[str, str]) -> Proposal:
    return Proposal("too-hard", easier[0], dict(target), f"{name} was too hard → {easier[1]}")


def _reps(exercise, name, target, reps, planned, done, rpe, easier, harder) -> Proposal:
    lo, hi = target["reps"]
    all_top = done >= planned and all(r >= hi for r in reps)
    missed = done < planned or any(r < lo for r in reps)
    did = f"{' / '.join(map(str, reps))} reps at effort {rpe}"

    if all_top and rpe <= 6 or all_top and rpe <= 8 and hi >= MAX_TOP_REPS:
        if harder:
            return Proposal("up", harder[0], {**target, "reps": [lo, hi] if hi < MAX_TOP_REPS else [8, 12]},
                            f"{did}: top of the range → {harder[1]}")
        return Proposal("add", exercise, {**target, "reps": [lo + 2, hi + 2]}, f"{did}: hardest variation, +2 reps")
    if all_top and rpe <= 8:
        return Proposal("add", exercise, {**target, "reps": [lo + 1, hi + 1]}, f"{did}: +1 rep per set")
    if missed or rpe >= 9 and not all_top:
        why = f"{_plural(done, 'set')} of {planned}" if done < planned else did
        if easier:
            return Proposal("down", easier[0], dict(target), f"{why}: too hard → {easier[1]}")
        new = [max(3, lo - 2), max(5, hi - 2)]
        return Proposal("down", exercise, {**target, "reps": new}, f"{why}: easiest variation, −2 reps")
    return Proposal("hold", exercise, dict(target), f"{did}: same again, aim for {hi} on every set")


def _hold(exercise, name, target, seconds, planned, done, rpe, easier, harder) -> Proposal:
    goal = target["seconds"]
    all_top = done >= planned and all(s >= goal for s in seconds)
    missed = done < planned or any(s < goal * 0.8 for s in seconds)
    did = f"{' / '.join(str(round(s)) for s in seconds)} s at effort {rpe}"

    if all_top and rpe <= 6:
        if goal >= HOLD_UP_AT and harder:
            return Proposal("up", harder[0], {**target, "seconds": HOLD_RESTART}, f"{did}: {goal} s held → {harder[1]}")
        return Proposal("add", exercise, {**target, "seconds": min(HOLD_MAX, goal + 10)}, f"{did}: +10 s")
    if all_top and rpe <= 8:
        return Proposal("add", exercise, {**target, "seconds": min(HOLD_MAX, goal + 5)}, f"{did}: +5 s")
    if missed or rpe >= 9 and not all_top:
        why = f"{_plural(done, 'set')} of {planned}" if done < planned else did
        if easier:
            return Proposal("down", easier[0], {**target, "seconds": max(15, goal - 10)}, f"{why}: too hard → {easier[1]}")
        return Proposal("down", exercise, {**target, "seconds": max(10, goal - 10)}, f"{why}: −10 s")
    return Proposal("hold", exercise, dict(target), f"{did}: same again")

"""Fitting a session plan into the time a lock leaves, from the athlete's measured pace."""

from __future__ import annotations

from sportlock.shared_kernel.plans import PlannedExercise, SessionPlan

MIN_TIMED_SECONDS = 120


def estimate_seconds(
    items: list[PlannedExercise] | tuple[PlannedExercise, ...], *, pace: float = 1.0, transition: int = 0
) -> int:
    """Expected session length: sets × (work + rest) scaled by the measured pace, plus the
    measured gap between exercises."""
    work = sum(item.target.naive_seconds() for item in items)
    return int(work * pace + transition * len(items))


def fit_plan(plan: SessionPlan, minutes: float, *, pace: float = 1.0, transition: int = 0) -> SessionPlan:
    """Shrink the plan to fit the lock: fewer sets first, then shorter warm-up/cool-down, then
    drop main exercises from the end. Warm-up and cool-down (first/last) always stay."""
    items = list(plan.items)
    budget = minutes * 60

    def too_long() -> bool:
        return estimate_seconds(items, pace=pace, transition=transition) > budget

    def main() -> range:
        return range(1, len(items) - 1) if len(items) > 2 else range(0)

    while too_long():
        reducible = [i for i in main() if items[i].target.sets > 2]
        if reducible:
            i = max(reducible, key=lambda i: items[i].target.sets)
            items[i] = items[i].model_copy(update={"target": items[i].target.with_(sets=items[i].target.sets - 1)})
            continue
        ends = [
            i
            for i in {0, len(items) - 1}
            if items[i].target.seconds and items[i].target.sets == 1 and items[i].target.seconds > MIN_TIMED_SECONDS
        ]
        if ends:
            for i in ends:
                seconds = max(MIN_TIMED_SECONDS, items[i].target.seconds - 60)  # type: ignore[operator]
                items[i] = items[i].model_copy(update={"target": items[i].target.with_(seconds=seconds)})
            continue
        if len(main()) > 1:
            items.pop(-2)
            continue
        break
    return plan.with_items(items)

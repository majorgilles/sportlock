"""Recovery-aware scheduling: how fast the athlete really trains, how much load they carry, and
what each scheduled lock should be — a hard session, a (shorter) recovery session, or a rest day.

The coach proposes what the next lock should be (`next_lock` in its plan); these rules make the
final call and enforce the guardrails, so a rest day can never become a loophole:
- rest only when allowed in settings, when the previous credited session is less than 36 h old,
  when there were at least `min_sessions_per_week` credited sessions in the last 7 days, and
  when fewer than `max_rest_days_in_a_row` of the last scheduled days were rest days;
- otherwise rest falls back to a recovery session.
Without a coach plan: recovery within 48 h of a hard session, hard otherwise.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Literal

from sportlock.shared_kernel.base import ValueObject
from sportlock.training.domain.repositories import ExerciseTiming, SessionSummary

DEFAULT_PACE = 1.4  # real time / naive estimate, until there is history
PACE_MIN, PACE_MAX = 1.0, 3.0
DEFAULT_TRANSITION = 45  # seconds between exercises, until measured
TRANSITION_MIN, TRANSITION_MAX = 15, 150
REST_WINDOW = timedelta(hours=36)
HARD_GAP = timedelta(hours=48)


class Policy(ValueObject):
    """The guardrails from settings."""

    allow_rest_days: bool = True
    max_rest_days_in_a_row: int = 2
    min_sessions_per_week: int = 3
    recovery_minutes: int = 15


class CoachAdvice(ValueObject):
    """What the coach advised for the next lock."""

    mode: Literal["auto", "recovery", "rest"] = "auto"
    recovery_minutes: int | None = None
    reason: str = ""


class RecoveryState(ValueObject):
    """The history the decision needs, read before deciding."""

    last_credited: datetime | None
    last_hard: datetime | None
    sessions_last_7_days: int
    rest_days_in_a_row: int


class LockDecision(ValueObject):
    """What a scheduled lock should be."""

    mode: Literal["hard", "recovery", "rest"]
    minutes: int  # lock length (shortened for recovery; 0 for rest)
    reason: str


def pace_factor(timings: list[ExerciseTiming]) -> float:
    """Ratio of real exercise time (first set start → rated) to the naive estimate. Captures
    slower reps, longer rests and logging time."""
    estimated = actual = 0.0
    used = 0
    for timing in timings:
        seconds = (timing.ended_at - timing.started_at).total_seconds()
        guess = timing.target.naive_seconds(timing.sets_done)
        if not timing.sets_done or guess <= 0 or seconds <= 0:
            continue
        estimated += guess
        actual += seconds
        used += 1
    if used < 3:
        return DEFAULT_PACE
    return round(min(PACE_MAX, max(PACE_MIN, actual / estimated)), 2)


def transition_seconds(gaps: list[float]) -> int:
    """Average gap between exercises (reading the card, getting set up). The average, not the
    median: the occasional long gap is exactly what makes sessions overrun."""
    usable = [g for g in gaps if 0 <= g <= 600]
    if len(usable) < 3:
        return DEFAULT_TRANSITION
    return int(min(TRANSITION_MAX, max(TRANSITION_MIN, sum(usable) / len(usable))))


def rest_days_in_a_row(rest_days: set[date], today: date) -> int:
    """Consecutive rest days immediately before `today`."""
    count, day = 0, today - timedelta(days=1)
    while day in rest_days:
        count += 1
        day -= timedelta(days=1)
    return count


def load_summary(week: list[SessionSummary], rest_days: int, now: datetime) -> dict:
    """The last week's load, as the coach and the calendar see it."""

    def entry(s: SessionSummary) -> dict:
        return {
            "date": s.day.isoformat(),
            "finished": s.finished_at.isoformat(timespec="seconds"),
            "day_type": s.day_type,
            "title": s.title,
            "minutes": s.minutes,
            "rpe": s.rpe,
            "load": s.minutes * (s.rpe or 5),
        }

    entries = [entry(s) for s in week]
    return {
        "sessions_last_7_days": len(entries),
        "minutes_last_7_days": sum(e["minutes"] for e in entries),
        "load_last_7_days": sum(e["load"] for e in entries),
        "last_48h": [e for s, e in zip(week, entries) if s.finished_at >= now - HARD_GAP],
        "rest_days_in_a_row": rest_days,
    }


def rest_allowed(state: RecoveryState, policy: Policy, now: datetime) -> tuple[bool, str]:
    """Whether a rest day is allowed now, and why not."""
    if not policy.allow_rest_days:
        return False, "rest days are off in settings"
    if state.last_credited is None or now - state.last_credited > REST_WINDOW:
        return False, "no session in the last 36 h to recover from"
    if state.sessions_last_7_days < policy.min_sessions_per_week:
        return False, f"fewer than {policy.min_sessions_per_week} sessions in the last 7 days"
    if state.rest_days_in_a_row >= policy.max_rest_days_in_a_row:
        return False, f"already {policy.max_rest_days_in_a_row} rest days in a row"
    return True, ""


def decide_lock(
    state: RecoveryState, policy: Policy, *, now: datetime, window_minutes: int, advice: CoachAdvice | None
) -> LockDecision:
    """What a scheduled lock starting now should be."""
    advice = advice or CoachAdvice()
    hard_recent = state.last_hard is not None and now - state.last_hard < HARD_GAP
    mode, reason = advice.mode, advice.reason
    recovery_minutes = min(window_minutes, int(advice.recovery_minutes or policy.recovery_minutes))

    if mode == "rest":
        allowed, why_not = rest_allowed(state, policy, now)
        if allowed:
            return LockDecision(mode="rest", minutes=0, reason=reason or "Recovery after your last session")
        mode, reason = "recovery", f"Rest day not possible ({why_not}); light session instead"
    if mode == "recovery" or (mode == "auto" and hard_recent):
        if not reason:
            hours = int((now - state.last_hard).total_seconds() // 3600) if state.last_hard else 0
            reason = f"Hard session {hours} h ago" if hard_recent else "Recovery"
        return LockDecision(mode="recovery", minutes=recovery_minutes, reason=reason)
    return LockDecision(mode="hard", minutes=window_minutes, reason=reason)

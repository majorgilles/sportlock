"""Recovery-aware scheduling: how fast the user really trains, how much load they carry, and
what each scheduled lock should be — a hard session, a (shorter) recovery session, or a rest day.

The coach proposes what the next lock should be (`next_lock` in its plan); the rules here make
the final call and enforce the guardrails, so a rest day can never become a loophole:
- rest only when allowed in settings, when the previous credited session is less than 36 h old,
  when there were at least `min_sessions_per_week` credited sessions in the last 7 days, and
  when fewer than `max_rest_days_in_a_row` of the last scheduled days were rest days;
- otherwise rest falls back to a recovery session.
Without a coach plan: recovery within 48 h of a hard session, hard otherwise.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .store import Store

DEFAULT_PACE = 1.4  # real time / naive estimate, until there is history
PACE_MIN, PACE_MAX = 1.0, 3.0
DEFAULT_TRANSITION = 45  # seconds between exercises (reading the card, getting in position), until measured
TRANSITION_MIN, TRANSITION_MAX = 15, 150
REST_WINDOW = timedelta(hours=36)
HARD_GAP = timedelta(hours=48)
COUNTED = "status = 'finished' AND kind NOT IN ('test', 'placeholder', 'outside')"


@dataclass(frozen=True)
class Policy:
    allow_rest_days: bool = True
    max_rest_days_in_a_row: int = 2
    min_sessions_per_week: int = 3
    recovery_minutes: int = 15


@dataclass(frozen=True)
class LockPlan:
    mode: str  # hard | recovery | rest
    minutes: int  # lock length (shortened for recovery; 0 for rest)
    reason: str


# -- pace ----------------------------------------------------------------------------------------


def naive_seconds(target: dict, sets: int | None = None) -> int:
    work = target["reps"][1] * 3 if "reps" in target else target.get("seconds", 0)
    work *= 2 if target.get("sides") else 1  # reps and seconds count per side
    return (sets or target["sets"]) * (work + target.get("rest", 0))


def pace_factor(store: Store, *, sessions: int = 10) -> float:
    """Ratio of real exercise time (first set start → rated) to the naive estimate, over recent
    exercises. Captures slower reps, longer rests and logging time."""
    rows = store.db.execute(
        "SELECT e.target, e.started_at, e.ended_at, (SELECT COUNT(*) FROM sets s WHERE s.session_exercise_id = e.id) AS n"
        " FROM session_exercises e JOIN sessions x ON x.id = e.session_id"
        " WHERE e.status = 'done' AND e.started_at IS NOT NULL AND e.ended_at IS NOT NULL"
        " AND x.kind NOT IN ('test', 'placeholder') AND e.session_id IN"
        " (SELECT id FROM sessions ORDER BY id DESC LIMIT ?)",
        (sessions,),
    ).fetchall()
    estimated = actual = 0.0
    used = 0
    for row in rows:
        if not row["n"]:
            continue
        seconds = (datetime.fromisoformat(row["ended_at"]) - datetime.fromisoformat(row["started_at"])).total_seconds()
        guess = naive_seconds(json.loads(row["target"]), row["n"])
        if guess <= 0 or seconds <= 0:
            continue
        estimated += guess
        actual += seconds
        used += 1
    if used < 3:
        return DEFAULT_PACE
    return round(min(PACE_MAX, max(PACE_MIN, actual / estimated)), 2)


def transition_seconds(store: Store, *, sessions: int = 10) -> int:
    """Average time between exercises in recent sessions — from the lock starting to the first
    exercise, and from one exercise ending to the next starting (reading the card, getting set up).
    The average, not the median: the occasional long gap is exactly what makes sessions overrun."""
    gaps = []
    for session in store.db.execute(
        "SELECT id, started_at FROM sessions WHERE kind NOT IN ('test', 'placeholder') AND started_at IS NOT NULL"
        " ORDER BY id DESC LIMIT ?", (sessions,)
    ).fetchall():
        rows = store.db.execute("SELECT started_at, ended_at FROM session_exercises WHERE session_id = ?"
                                " AND started_at IS NOT NULL ORDER BY started_at", (session["id"],)).fetchall()
        previous_end = session["started_at"]
        for row in rows:
            if previous_end:
                gap = (datetime.fromisoformat(row["started_at"]) - datetime.fromisoformat(previous_end)).total_seconds()
                if 0 <= gap <= 600:
                    gaps.append(gap)
            previous_end = row["ended_at"]
    if len(gaps) < 3:
        return DEFAULT_TRANSITION
    return int(min(TRANSITION_MAX, max(TRANSITION_MIN, sum(gaps) / len(gaps))))


# -- load ----------------------------------------------------------------------------------------


def sessions_since(store: Store, since: datetime) -> list[dict]:
    """Credited sessions since `since`, with minutes, effort and session-RPE load."""
    result = []
    for row in store.db.execute(
        f"SELECT id, day, started_at, finished_at, day_type, rpe, title FROM sessions WHERE {COUNTED}"
        " AND finished_at >= ? ORDER BY finished_at", (since.isoformat(timespec="seconds"),)
    ):
        started = datetime.fromisoformat(row["started_at"]) if row["started_at"] else None
        finished = datetime.fromisoformat(row["finished_at"])
        minutes = round((finished - started).total_seconds() / 60) if started else 0
        rpe = row["rpe"]
        if rpe is None:  # ran out of time before the summary: average the exercises
            value = store.db.execute("SELECT AVG(rpe) FROM session_exercises WHERE session_id = ? AND rpe IS NOT NULL",
                                     (row["id"],)).fetchone()[0]
            rpe = round(value) if value else None
        result.append({"date": row["day"], "finished": row["finished_at"], "day_type": row["day_type"],
                       "title": row["title"], "minutes": minutes, "rpe": rpe, "load": minutes * (rpe or 5)})
    return result


def last_credited(store: Store) -> datetime | None:
    row = store.db.execute(f"SELECT MAX(finished_at) FROM sessions WHERE {COUNTED}").fetchone()
    return datetime.fromisoformat(row[0]) if row and row[0] else None


def last_hard(store: Store) -> datetime | None:
    row = store.db.execute(f"SELECT MAX(finished_at) FROM sessions WHERE {COUNTED} AND day_type = 'hard'").fetchone()
    return datetime.fromisoformat(row[0]) if row and row[0] else None


def rest_days_in_a_row(store: Store, today: date) -> int:
    """Consecutive rest days immediately before `today` (by scheduled locks marked as rest)."""
    rest_days = {date.fromisoformat(row[0][:10]) for row in
                 store.db.execute("SELECT start FROM lock_events WHERE outcome = 'rest'")}
    count, day = 0, today - timedelta(days=1)
    while day in rest_days:
        count += 1
        day -= timedelta(days=1)
    return count


def load_summary(store: Store, now: datetime) -> dict:
    week = sessions_since(store, now - timedelta(days=7))
    return {
        "sessions_last_7_days": len(week),
        "minutes_last_7_days": sum(s["minutes"] for s in week),
        "load_last_7_days": sum(s["load"] for s in week),
        "last_48h": [s for s in week if datetime.fromisoformat(s["finished"]) >= now - HARD_GAP],
        "rest_days_in_a_row": rest_days_in_a_row(store, now.date()),
    }


# -- the decision ---------------------------------------------------------------------------------


def rest_allowed(store: Store, policy: Policy, now: datetime) -> tuple[bool, str]:
    if not policy.allow_rest_days:
        return False, "rest days are off in settings"
    previous = last_credited(store)
    if previous is None or now - previous > REST_WINDOW:
        return False, "no session in the last 36 h to recover from"
    if len(sessions_since(store, now - timedelta(days=7))) < policy.min_sessions_per_week:
        return False, f"fewer than {policy.min_sessions_per_week} sessions in the last 7 days"
    if rest_days_in_a_row(store, now.date()) >= policy.max_rest_days_in_a_row:
        return False, f"already {policy.max_rest_days_in_a_row} rest days in a row"
    return True, ""


def plan_lock(store: Store, policy: Policy, *, now: datetime, window_minutes: int, coach: dict | None) -> LockPlan:
    """What a scheduled lock starting now should be. `coach` is the plan's `next_lock`
    ({"mode": auto|recovery|rest, "recovery_minutes": int|None, "reason": str}) or None."""
    hard_recent = (lh := last_hard(store)) is not None and now - lh < HARD_GAP
    mode = (coach or {}).get("mode", "auto")
    reason = (coach or {}).get("reason", "")
    recovery_minutes = min(window_minutes, int((coach or {}).get("recovery_minutes") or policy.recovery_minutes))

    if mode == "rest":
        allowed, why_not = rest_allowed(store, policy, now)
        if allowed:
            return LockPlan("rest", 0, reason or "Recovery after your last session")
        mode, reason = "recovery", f"Rest day not possible ({why_not}); light session instead"
    if mode == "recovery" or (mode == "auto" and hard_recent):
        if not reason:
            reason = f"Hard session {int((now - lh).total_seconds() // 3600)} h ago" if hard_recent else "Recovery"
        return LockPlan("recovery", recovery_minutes, reason)
    return LockPlan("hard", window_minutes, reason)

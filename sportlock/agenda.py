"""Calendar data for the app: what happened on past days and what is planned for coming ones."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from . import recovery
from .config import Config
from .library import Library
from .schedule import windows_for_day
from .store import Store

HIDDEN_KINDS = ("test", "placeholder")


def _session_details(store: Store, library: Library, session_id: int) -> list[dict]:
    exercises = []
    for e in store.db.execute("SELECT * FROM session_exercises WHERE session_id = ? ORDER BY id", (session_id,)):
        if e["status"] == "swapped":
            continue  # its easier replacement follows
        sets = [dict(s) for s in store.db.execute(
            "SELECT reps, seconds, load_kg FROM sets WHERE session_exercise_id = ? ORDER BY set_no", (e["id"],))]
        exercises.append({"name": e["name"], "status": e["status"], "rpe": e["rpe"], "note": e["note"] or "",
                          "skip_reason": e["skip_reason"] or "", "target": json.loads(e["target"]), "sets": sets})
    return exercises


def _plan_details(library: Library, version: dict) -> list[dict]:
    return [{"name": library.get(item["exercise"])["name"],
             "target": {k: v for k, v in item.items() if k != "exercise"} | {"sides": library.get(item["exercise"]).get("sides")}}
            for item in version.get("plan", [])]


def build(store: Store, library: Library, config: Config, *, now: datetime, coach_plan: dict | None,
          lock_plans: dict, policy: recovery.Policy, days_back: int = 14, days_ahead: int = 14) -> dict:
    today = now.date()
    first = today - timedelta(days=days_back)
    first -= timedelta(days=first.weekday())  # start on a Monday
    last = today + timedelta(days=days_ahead)
    last += timedelta(days=6 - last.weekday())  # end on a Sunday

    days: dict[str, dict] = {}
    day = first
    while day <= last:
        days[day.isoformat()] = {"date": day.isoformat(), "today": day == today, "past": day < today,
                                 "sessions": [], "locks": []}
        day += timedelta(days=1)

    # What happened.
    for s in store.db.execute(
        "SELECT * FROM sessions WHERE day BETWEEN ? AND ? AND kind NOT IN ('test', 'placeholder') AND status != 'in_progress'"
        " ORDER BY started_at", (first.isoformat(), last.isoformat())
    ):
        started = s["started_at"] and datetime.fromisoformat(s["started_at"])
        finished = datetime.fromisoformat(s["finished_at"])
        days[s["day"]]["sessions"].append({
            "id": s["id"], "title": s["title"] or "Session", "day_type": s["day_type"], "status": s["status"],
            "kind": s["kind"], "time": started.strftime("%H:%M") if started else "",
            "minutes": round((finished - started).total_seconds() / 60) if started else None,
            "rpe": s["rpe"], "notes": s["notes"] or "", "source": s["plan_source"],
            "exercises": _session_details(store, library, s["id"]),
            "recommendations": coach_plan.get("recommendations", [])
                               if coach_plan and coach_plan.get("feedback_session") == s["id"] else [],
        })
    for event in store.db.execute("SELECT * FROM lock_events WHERE substr(start, 1, 10) BETWEEN ? AND ?",
                                  (first.isoformat(), last.isoformat())):
        key_day = event["start"][:10]
        if key_day in days and event["outcome"] in ("rest", "override", "expired"):
            plan = lock_plans.get(event["key"], {})
            days[key_day]["locks"].append({"time": event["start"][11:16], "outcome": event["outcome"],
                                           "reason": plan.get("reason", "")})

    # What's planned: every scheduled lock from now on; the next one in detail.
    trained = store.trained_days()
    ended = store.ended_early()
    next_done = False
    day = today
    while config.enabled and day <= last:
        for window in windows_for_day(config, day):
            if window.end <= now or window.key in ended or window.day in trained:
                continue
            minutes = int((window.end - window.start).total_seconds() // 60)
            entry = {"time": window.start.strftime("%H:%M"), "scheduled_minutes": minutes}
            if window.key in lock_plans:
                plan = lock_plans[window.key]
                entry.update(mode=plan["mode"], minutes=plan["minutes"], reason=plan["reason"], decided=True)
            elif not next_done:
                decided = recovery.plan_lock(store, policy, now=window.start, window_minutes=minutes,
                                             coach=coach_plan.get("next_lock") if coach_plan else None)
                entry.update(mode=decided.mode, minutes=decided.minutes, reason=decided.reason, decided=False)
            else:
                entry.update(mode="later", minutes=minutes, reason="Planned after your next session", decided=False)
            if not next_done:
                entry["next"] = True
                if coach_plan and entry["mode"] in ("hard", "recovery"):
                    version = coach_plan[entry["mode"]]
                    entry.update(title=version.get("title", ""), rationale=coach_plan.get("rationale", ""),
                                 exercises=_plan_details(library, version),
                                 recommendations=coach_plan.get("recommendations", []))
                next_done = True
            days[day.isoformat()].setdefault("planned", []).append(entry)
        day += timedelta(days=1)

    return {"today": today.isoformat(), "days": list(days.values()),
            "load": recovery.load_summary(store, now)}

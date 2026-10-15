"""The app's calendar: what happened on past days and what is planned for coming ones."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sportlock.coaching.application.fresh_plan import CoachPlanFreshness
from sportlock.coaching.domain.plan import CoachPlan
from sportlock.exercises.domain.catalogue import Catalogue
from sportlock.locks.application.lock_services import policy_of
from sportlock.locks.domain.locks import EARLY_OUTCOMES, ended_early, rest_days
from sportlock.locks.domain.repositories import (
    LockEventFilters,
    LockEventRepositoryProtocol,
    LockPlanFilters,
    LockPlanRepositoryProtocol,
)
from sportlock.locks.domain.schedule import Window, windows_for_day
from sportlock.recovery.domain.policy import CoachAdvice, RecoveryState, decide_lock, load_summary, rest_days_in_a_row
from sportlock.settings.domain.settings import Settings
from sportlock.shared_kernel.plans import PlannedExercise, SessionPlan
from sportlock.training.application.training_history import TrainingHistory


def _empty_days(first: date, last: date, today: date) -> dict[str, dict]:
    """One entry per day from first to last, keyed by ISO date."""
    days: dict[str, dict] = {}
    day = first
    while day <= last:
        days[day.isoformat()] = {
            "date": day.isoformat(),
            "today": day == today,
            "past": day < today,
            "sessions": [],
            "locks": [],
        }
        day += timedelta(days=1)
    return days


class BuildCalendarService:
    """Weeks of days (Monday to Sunday) around today."""

    def __init__(
        self,
        training_history: TrainingHistory,
        lock_event_repository: LockEventRepositoryProtocol,
        lock_plan_repository: LockPlanRepositoryProtocol,
        coach_plan_freshness: CoachPlanFreshness,
        catalogue: Catalogue,
    ) -> None:
        self.training_history = training_history
        self.lock_event_repository = lock_event_repository
        self.lock_plan_repository = lock_plan_repository
        self.coach_plan_freshness = coach_plan_freshness
        self.catalogue = catalogue

    def execute(self, settings: Settings, now: datetime, *, days_back: int = 14, days_ahead: int = 14) -> dict:
        """The calendar, shaped {"today", "days": [...], "load"}."""
        today = now.date()
        first = today - timedelta(days=days_back)
        first -= timedelta(days=first.weekday())
        last = today + timedelta(days=days_ahead)
        last += timedelta(days=6 - last.weekday())
        days = _empty_days(first, last, today)

        coach = self.coach_plan_freshness.fresh_plan()
        plans = {plan.key: plan for plan in self.lock_plan_repository.find(LockPlanFilters())}
        self._add_sessions(days, first, last, coach)
        self._add_lock_outcomes(days, first, last, plans)
        self._add_upcoming_locks(days, settings, now, coach, plans)

        week = self.training_history.credited_since(now - timedelta(days=7))
        return {
            "today": today.isoformat(),
            "days": list(days.values()),
            "load": load_summary(
                week,
                rest_days_in_a_row(
                    rest_days(self.lock_event_repository.find(LockEventFilters(outcomes=("rest",)))), today
                ),
                now,
            ),
        }

    def _add_sessions(self, days: dict[str, dict], first: date, last: date, coach: CoachPlan | None) -> None:
        for s in self.training_history.between(first, last):
            started = s["started_at"] and datetime.fromisoformat(s["started_at"])
            finished = datetime.fromisoformat(s["finished_at"])
            days[s["day"]]["sessions"].append(
                {
                    "id": s["id"],
                    "title": s["title"] or "Session",
                    "day_type": s["day_type"],
                    "status": s["status"],
                    "kind": s["kind"],
                    "time": started.strftime("%H:%M") if started else "",
                    "minutes": round((finished - started).total_seconds() / 60) if started else None,
                    "rpe": s["rpe"],
                    "notes": s["notes"] or "",
                    "source": s["plan_source"],
                    "exercises": s["exercises"],
                    "recommendations": [r.model_dump() for r in coach.recommendations]
                    if coach and coach.feedback_session == s["id"]
                    else [],
                }
            )

    def _add_lock_outcomes(self, days: dict[str, dict], first: date, last: date, plans: dict) -> None:
        for event in self.lock_event_repository.find(LockEventFilters(first_day=first, last_day=last)):
            key_day = event.start[:10]
            if key_day in days and event.outcome in ("rest", "override", "expired"):
                plan = plans.get(event.key)
                days[key_day]["locks"].append(
                    {"time": event.start[11:16], "outcome": event.outcome, "reason": plan.reason if plan else ""}
                )

    def _add_upcoming_locks(
        self, days: dict[str, dict], settings: Settings, now: datetime, coach: CoachPlan | None, plans: dict
    ) -> None:
        last = date.fromisoformat(next(reversed(days)))
        trained, ended = (
            self.training_history.trained_days(),
            ended_early(self.lock_event_repository.find(LockEventFilters(outcomes=EARLY_OUTCOMES))),
        )
        advice = CoachAdvice(**coach.next_lock.model_dump()) if coach else None
        next_done = False
        day = now.date()
        while settings.enabled and day <= last:
            for window in windows_for_day(settings, day):
                if window.end <= now or window.key in ended or window.day in trained:
                    continue
                entry = self._upcoming_entry(window, settings, plans, advice, decide=not next_done)
                if not next_done:
                    self._mark_next(entry, coach)
                    next_done = True
                days[day.isoformat()].setdefault("planned", []).append(entry)
            day += timedelta(days=1)

    def _upcoming_entry(
        self, window: Window, settings: Settings, plans: dict, advice: CoachAdvice | None, *, decide: bool
    ) -> dict:
        """One upcoming lock: its stored plan, a fresh decision for the next one, or "later"."""
        entry: dict = {"time": window.start.strftime("%H:%M"), "scheduled_minutes": window.minutes}
        if window.key in plans:
            plan = plans[window.key]
            entry.update(mode=plan.mode, minutes=plan.minutes, reason=plan.reason, decided=True)
        elif decide:
            decided = decide_lock(
                self._state(window.start),
                policy_of(settings),
                now=window.start,
                window_minutes=window.minutes,
                advice=advice,
            )
            entry.update(mode=decided.mode, minutes=decided.minutes, reason=decided.reason, decided=False)
        else:
            entry.update(mode="later", minutes=window.minutes, reason="Planned after your next session", decided=False)
        return entry

    def _mark_next(self, entry: dict, coach: CoachPlan | None) -> None:
        """The next lock shows the coach's session in detail."""
        entry["next"] = True
        if coach and entry["mode"] in ("hard", "recovery"):
            version: SessionPlan = getattr(coach, entry["mode"])
            entry.update(
                title=version.title,
                rationale=coach.rationale,
                exercises=[self._planned(i) for i in version.items],
                recommendations=[r.model_dump() for r in coach.recommendations],
            )

    def _state(self, at: datetime) -> RecoveryState:
        return RecoveryState(
            last_credited=self.training_history.last_credited(),
            last_hard=self.training_history.last_hard(),
            sessions_last_7_days=len(self.training_history.credited_since(at - timedelta(days=7))),
            rest_days_in_a_row=rest_days_in_a_row(
                rest_days(self.lock_event_repository.find(LockEventFilters(outcomes=("rest",)))), at.date()
            ),
        )

    def _planned(self, item: PlannedExercise) -> dict:
        exercise = self.catalogue.find(item.exercise)
        return {
            "name": self.catalogue.name(item.exercise),
            "target": {**item.target.to_dict(), "sides": exercise.sides if exercise else None},
        }

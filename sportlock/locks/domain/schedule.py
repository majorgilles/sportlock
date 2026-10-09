"""Which lock windows exist and which one applies right now.

Everything works on wall-clock datetimes passed in by the caller, so time spent asleep or
powered off simply shrinks the remaining part of a window.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sportlock.settings.domain.settings import Settings
from sportlock.shared_kernel.base import ValueObject

FREEZE_MINUTES = 10  # settings edits wait while a lock is due within this many minutes
LOOKAHEAD_DAYS = 8


class Window(ValueObject):
    """A scheduled lock's time span; its key (start, to the minute) identifies it everywhere."""

    start: datetime
    end: datetime

    @property
    def key(self) -> str:
        """Identity of the window, e.g. 2026-10-05T18:00."""
        return self.start.strftime("%Y-%m-%dT%H:%M")

    @property
    def day(self) -> date:
        """The day the window starts on."""
        return self.start.date()

    @property
    def minutes(self) -> int:
        """Length in whole minutes."""
        return int((self.end - self.start).total_seconds() // 60)

    def contains(self, now: datetime) -> bool:
        """Whether `now` falls inside the window."""
        return self.start <= now < self.end


class Decision(ValueObject):
    """What the schedule says right now."""

    active: Window | None  # window that should be locking right now
    next: Window | None  # next window that will lock
    warning: int | None  # most urgent warning threshold (minutes) already crossed for `next`


def windows_for_day(settings: Settings, day: date) -> list[Window]:
    """Windows starting on `day`: overlaps merged, then truncated to the daily cap in order."""
    raw = sorted((datetime.combine(day, entry.at), timedelta(minutes=entry.minutes))
                 for entry in settings.locks if day.weekday() in entry.days)
    merged: list[list[datetime]] = []
    for start, length in raw:
        end = start + length
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    windows = []
    budget = timedelta(minutes=settings.max_minutes_per_day)
    for start, end in merged:
        if budget <= timedelta(0):
            break
        length = min(end - start, budget)
        windows.append(Window(start=start, end=start + length))
        budget -= length
    return windows


def windows_around(settings: Settings, now: datetime) -> list[Window]:
    """Windows from yesterday (one may run past midnight) through the next week, so the next
    lock is found even across a weekend or a schedule with few days."""
    today = now.date()
    days = [today + timedelta(days=offset) for offset in range(-1, LOOKAHEAD_DAYS)]
    return [window for day in days for window in windows_for_day(settings, day)]


def decide(settings: Settings, now: datetime, *, trained_days: set[date], ended: set[str]) -> Decision:
    """`trained_days`: days with a finished session (their windows are skipped).
    `ended`: keys of windows already ended early (session finished, override or rest day)."""
    if not settings.enabled:
        return Decision(active=None, next=None, warning=None)
    live = [w for w in windows_around(settings, now) if w.day not in trained_days and w.key not in ended]
    active = next((w for w in live if w.contains(now)), None)
    upcoming = next((w for w in live if w.start > now), None)
    warning = None
    if upcoming is not None:
        crossed = [m for m in settings.warn_minutes if upcoming.start - timedelta(minutes=m) <= now]
        warning = min(crossed) if crossed else None
    return Decision(active=active, next=upcoming, warning=warning)


def settings_frozen(decision: Decision, now: datetime) -> bool:
    """True while a lock is active or due within FREEZE_MINUTES: settings edits must wait."""
    if decision.active is not None:
        return True
    return decision.next is not None and decision.next.start - timedelta(minutes=FREEZE_MINUTES) <= now

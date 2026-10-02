"""Pure scheduling logic: which lock windows exist and which one applies right now.

Everything here works on wall-clock datetimes passed in by the caller, so time spent asleep or
powered off simply shrinks the remaining part of a window.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .config import Config

FREEZE_MINUTES = 10


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime

    @property
    def key(self) -> str:
        return self.start.strftime("%Y-%m-%dT%H:%M")

    @property
    def day(self) -> date:
        return self.start.date()

    def contains(self, now: datetime) -> bool:
        return self.start <= now < self.end


@dataclass(frozen=True)
class Decision:
    active: Window | None  # window that should be locking right now
    next: Window | None  # next window that will lock
    warning: int | None  # most urgent warning threshold (minutes) already crossed for `next`


def windows_for_day(config: Config, day: date) -> list[Window]:
    """Windows starting on `day`: overlaps merged, then truncated to the daily cap in order."""
    raw = sorted(
        (datetime.combine(day, entry.at), timedelta(minutes=entry.minutes))
        for entry in config.locks
        if day.weekday() in entry.days
    )

    merged: list[list[datetime]] = []
    for start, length in raw:
        end = start + length
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    windows = []
    budget = timedelta(minutes=config.max_minutes_per_day)
    for start, end in merged:
        if budget <= timedelta(0):
            break
        length = min(end - start, budget)
        windows.append(Window(start, start + length))
        budget -= length
    return windows


LOOKAHEAD_DAYS = 8


def windows_around(config: Config, now: datetime) -> list[Window]:
    """Windows from yesterday (one may run past midnight) through the next week, so the next
    lock is found even across a weekend or a schedule with few days."""
    today = now.date()
    days = [today + timedelta(days=offset) for offset in range(-1, LOOKAHEAD_DAYS)]
    return [window for day in days for window in windows_for_day(config, day)]


def decide(config: Config, now: datetime, *, trained_days: set[date], ended: set[str]) -> Decision:
    """`trained_days`: days with a finished session (their windows are skipped).
    `ended`: keys of windows already ended early (session finished or override)."""
    if not config.enabled:
        return Decision(None, None, None)

    live = [w for w in windows_around(config, now) if w.day not in trained_days and w.key not in ended]
    active = next((w for w in live if w.contains(now)), None)
    upcoming = next((w for w in live if w.start > now), None)

    warning = None
    if upcoming is not None:
        crossed = [m for m in config.warn_minutes if upcoming.start - timedelta(minutes=m) <= now]
        warning = min(crossed) if crossed else None

    return Decision(active, upcoming, warning)


def config_frozen(decision: Decision, now: datetime) -> bool:
    """True while a lock is active or due within FREEZE_MINUTES: config edits must wait."""
    if decision.active is not None:
        return True
    return decision.next is not None and decision.next.start - timedelta(minutes=FREEZE_MINUTES) <= now

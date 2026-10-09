"""Persistence ports for training: the session aggregate, and read models over past sessions."""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

from sportlock.shared_kernel.base import ValueObject
from sportlock.shared_kernel.targets import Target
from sportlock.training.domain.session import TrainingSession


class TrainingSessionRepositoryProtocol(Protocol):
    """The session aggregate."""

    def active(self) -> TrainingSession | None:
        """The session being trained right now, if any."""
        ...

    def add(self, session: TrainingSession) -> TrainingSession:
        """Store a new session (ids are assigned) and make it the active one."""
        ...

    def save(self, session: TrainingSession) -> None:
        """Store every change; a session that is no longer in progress stops being the active one."""
        ...

    def discard_active(self) -> None:
        """Forget the active run without touching its records (repair only)."""
        ...


class ExerciseTiming(ValueObject):
    """How long one done exercise really took, for the pace estimate."""

    target: Target
    sets_done: int
    started_at: datetime
    ended_at: datetime


class SessionSummary(ValueObject):
    """One credited session, for load and recovery decisions."""

    id: int
    day: date
    finished_at: datetime
    day_type: str | None
    title: str | None
    minutes: int
    rpe: int | None  # the session's effort, or the average of its exercises when it ran out of time


class TrainingHistoryProtocol(Protocol):
    """Read models over past sessions. "Credited" sessions are finished and neither test,
    placeholder nor outside sessions."""

    def trained_days(self) -> set[date]:
        """Days with a credited session."""
        ...

    def credited_since(self, since: datetime) -> list[SessionSummary]:
        """Credited sessions finished since `since`, oldest first."""
        ...

    def last_credited(self) -> datetime | None:
        """When the last credited session finished."""
        ...

    def last_hard(self) -> datetime | None:
        """When the last credited hard session finished."""
        ...

    def exercise_timings(self, sessions: int) -> list[ExerciseTiming]:
        """Done exercises of the last `sessions` sessions, with their real duration."""
        ...

    def transition_gaps(self, sessions: int) -> list[float]:
        """Seconds between the lock starting and the first exercise, and between exercises."""
        ...

    def times_done(self, exercise: str, excluding_session: int | None) -> int:
        """How often the exercise was done before."""
        ...

    def recent_details(self, since: date, limit: int) -> list[dict]:
        """Recent counted sessions in full (exercises, sets, efforts, notes), newest first."""
        ...

    def between(self, first: date, last: date) -> list[dict]:
        """Counted sessions between two days, with their exercises, for the calendar."""
        ...

    def last_counted_id(self) -> int | None:
        """Id of the latest counted, closed session."""
        ...

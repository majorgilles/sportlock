"""Persistence ports for training: the session aggregate, and read models over past sessions.

"Credited" sessions are finished and neither test, placeholder nor outside sessions. "Counted" sessions
are closed and neither test nor placeholder sessions.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

from sportlock.shared_kernel.base import ValueObject
from sportlock.shared_kernel.repositories import (
    CreateRepositoryProtocol,
    FindRepositoryProtocol,
    GetSingletonRepositoryProtocol,
    UpdateRepositoryProtocol,
)
from sportlock.shared_kernel.targets import Target
from sportlock.training.domain.session import TrainingSession


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


class TransitionGap(ValueObject):
    """Seconds between the lock starting and the first exercise, or between two exercises."""

    seconds: float


class CompletedExercise(ValueObject):
    """An exercise done in a session."""

    session_id: int
    exercise: str


class SessionSummaryFilters(ValueObject):
    """Which credited sessions to load: finished since a moment, of one day type; oldest finish first."""

    finished_since: datetime | None = None
    day_type: str | None = None


class ExerciseTimingFilters(ValueObject):
    """Which timings to load: the done exercises of the last `last_sessions` sessions."""

    last_sessions: int


class TransitionGapFilters(ValueObject):
    """Which gaps to load: those of the last `last_sessions` counted sessions."""

    last_sessions: int


class CompletedExerciseFilters(ValueObject):
    """Which done exercises to load: of one exercise, outside one session."""

    exercise: str | None = None
    excluding_session: int | None = None


class SessionDetailsFilters(ValueObject):
    """Which counted sessions to load in full for the coach: on or after `since`, the latest `latest`; newest first."""

    since: date
    latest: int


class CountedSessionFilters(ValueObject):
    """Which counted sessions to load with their exercises: between two days.

    By start time, or newest first (by id) with `newest_first`; `latest` keeps the first `latest` of that order.
    """

    first_day: date | None = None
    last_day: date | None = None
    newest_first: bool = False
    latest: int | None = None


class TrainingSessionRepositoryProtocol(
    CreateRepositoryProtocol[TrainingSession],
    UpdateRepositoryProtocol[TrainingSession],
    GetSingletonRepositoryProtocol[TrainingSession],
    Protocol,
):
    """The session aggregate.

    `create` stores a new session (ids are assigned) and makes it the active one; `get` is the session being
    trained right now, if any; `update` stores every change, and a session that is no longer in progress
    stops being the active one.
    """


class SessionSummaryRepositoryProtocol(FindRepositoryProtocol[SessionSummary, SessionSummaryFilters], Protocol):
    """Credited sessions, summarized."""


class ExerciseTimingRepositoryProtocol(FindRepositoryProtocol[ExerciseTiming, ExerciseTimingFilters], Protocol):
    """Real durations of done exercises."""


class TransitionGapRepositoryProtocol(FindRepositoryProtocol[TransitionGap, TransitionGapFilters], Protocol):
    """Gaps before and between exercises."""


class CompletedExerciseRepositoryProtocol(
    FindRepositoryProtocol[CompletedExercise, CompletedExerciseFilters], Protocol
):
    """Exercises done in past sessions."""


class SessionDetailsRepositoryProtocol(FindRepositoryProtocol[dict, SessionDetailsFilters], Protocol):
    """Counted sessions in full (exercises, sets, efforts, notes), in the JSON shape the coach reads."""


class CountedSessionRepositoryProtocol(FindRepositoryProtocol[dict, CountedSessionFilters], Protocol):
    """Counted sessions with their exercises, in the JSON shape the calendar and the report read."""

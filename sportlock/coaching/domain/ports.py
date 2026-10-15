"""What coaching needs from the outside world: the coach itself, and where its work is kept."""

from __future__ import annotations

from typing import Protocol

from sportlock.coaching.domain.memory import CoachMemory
from sportlock.coaching.domain.plan import CoachPlan
from sportlock.coaching.domain.replacements import ReplacementAnswer
from sportlock.coaching.domain.review import CoachReview
from sportlock.shared_kernel.base import ValueObject
from sportlock.shared_kernel.repositories import (
    CreateRepositoryProtocol,
    FindRepositoryProtocol,
    GetRepositoryProtocol,
    GetSingletonRepositoryProtocol,
    UpdateRepositoryProtocol,
    UpdateSingletonRepositoryProtocol,
)


class CoachProtocol(Protocol):
    """Writes the next session from what the app knows (a language model, in practice)."""

    def write_plan(self, context: dict) -> dict:
        """The coach's raw answer (validated by `parse_coach_output`). Raises CoachUnavailableError."""
        ...

    def write_review(self, context: dict) -> dict:
        """The coach's raw review of the trainee (validated by `parse_review`). Raises CoachUnavailableError."""
        ...


class CoachUnavailableError(RuntimeError):
    """The coach couldn't be reached or gave no usable answer."""


class CoachRun(ValueObject):
    """One attempt to plan, for diagnostics. `id` is set once stored."""

    at: str
    seconds: int
    ok: bool
    error: str | None = None
    id: int | None = None


class CoachRunFilters(ValueObject):
    """Which runs to load: the latest `latest` of them (all when None), oldest first."""

    latest: int | None = None


class MemoryNoteVersion(ValueObject):
    """One version of a memory note, from when it was written to when it changed or ended."""

    note_id: int
    topic: str
    text: str
    valid_from: str
    valid_to: str | None
    written_by: str | None
    ended_by: str | None
    end_reason: str | None


class MemoryNoteVersionFilters(ValueObject):
    """Which versions to load: by end reason, and the latest `latest` of them (all when None).

    Oldest first, unless `newest_first`.
    """

    end_reason: str | None = None
    latest: int | None = None
    newest_first: bool = False


class ReplacementAnswerFilters(ValueObject):
    """Which answers to load: those about one session, and the latest `latest` of them; oldest first."""

    session_id: int | None = None
    latest: int | None = None


class CoachReviewFilters(ValueObject):
    """Which reviews to load: those with a dossier only, and the latest `latest` of them; by session."""

    with_dossier: bool = False
    latest: int | None = None


class CoachPlanRepositoryProtocol(
    GetSingletonRepositoryProtocol[CoachPlan], UpdateSingletonRepositoryProtocol[CoachPlan], Protocol
):
    """The latest plan; it may be stale, so compare its basis."""


class CoachRunRepositoryProtocol(
    CreateRepositoryProtocol[CoachRun], FindRepositoryProtocol[CoachRun, CoachRunFilters], Protocol
):
    """Every coach run; `create` returns the run with its id."""


class CoachMemoryRepositoryProtocol(
    GetSingletonRepositoryProtocol[CoachMemory], UpdateSingletonRepositoryProtocol[CoachMemory], Protocol
):
    """The memory aggregate, persisted as versioned notes.

    `get` never returns None; `update` persists the events the aggregate recorded since it was loaded.
    """


class MemoryNoteVersionRepositoryProtocol(
    FindRepositoryProtocol[MemoryNoteVersion, MemoryNoteVersionFilters], Protocol
):
    """Every version of every memory note."""


class ReplacementAnswerRepositoryProtocol(
    CreateRepositoryProtocol[ReplacementAnswer],
    FindRepositoryProtocol[ReplacementAnswer, ReplacementAnswerFilters],
    Protocol,
):
    """The athlete's answers to replaced exercises."""


class CoachReviewRepositoryProtocol(
    GetRepositoryProtocol[CoachReview, int],
    FindRepositoryProtocol[CoachReview, CoachReviewFilters],
    CreateRepositoryProtocol[CoachReview],
    UpdateRepositoryProtocol[CoachReview],
    Protocol,
):
    """The coach's reviews, one per session, by session id."""

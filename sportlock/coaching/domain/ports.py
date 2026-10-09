"""What coaching needs from the outside world: the coach itself, and where its work is kept."""

from __future__ import annotations

from typing import Protocol

from sportlock.coaching.domain.memory import CoachMemory
from sportlock.coaching.domain.plan import CoachPlan
from sportlock.shared_kernel.base import ValueObject


class CoachProtocol(Protocol):
    """Writes the next session from what the app knows (a language model, in practice)."""

    def write_plan(self, context: dict) -> dict:
        """The coach's raw answer (validated by `parse_coach_output`). Raises CoachUnavailableError."""
        ...


class CoachUnavailableError(RuntimeError):
    """The coach couldn't be reached or gave no usable answer."""


class CoachRun(ValueObject):
    """One attempt to plan, for diagnostics."""

    at: str
    seconds: int
    ok: bool
    error: str | None = None


class CoachPlanRepositoryProtocol(Protocol):
    """The latest plan."""

    def get(self) -> CoachPlan | None:
        """The stored plan (it may be stale: compare its basis)."""
        ...

    def save(self, plan: CoachPlan) -> None:
        """Replace the plan."""
        ...


class CoachRunLogProtocol(Protocol):
    """Recent coach runs."""

    def add(self, run: CoachRun) -> int:
        """Record a run; returns its id."""
        ...

    def recent(self, limit: int) -> list[CoachRun]:
        """The latest runs, oldest first."""
        ...


class CoachMemoryRepositoryProtocol(Protocol):
    """The memory aggregate, persisted as versioned notes."""

    def load(self) -> CoachMemory:
        """The current notes."""
        ...

    def save(self, memory: CoachMemory) -> None:
        """Persist the events the aggregate recorded since it was loaded."""
        ...

    def forgotten(self, limit: int) -> list[str]:
        """Texts of notes the athlete removed, newest last."""
        ...

    def history(self, limit: int) -> list[dict]:
        """Every version of every note, newest first, as {note_id, topic, text, valid_from, valid_to,
        written_by, ended_by, end_reason}."""
        ...

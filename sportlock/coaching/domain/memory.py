"""The coach's long-term memory of the athlete: short notes it maintains across runs.

The coach changes the memory through explicit operations (add / update / delete a note by id),
never by rewriting it, so every change is an event with a history (see
docs/architecture-decision-records/2026_10_09_coach_memory_as_versioned_notes.md).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from sportlock.shared_kernel.base import Aggregate, DomainEvent, ValueObject
from sportlock.shared_kernel.errors import DomainError

type Topic = Literal["body", "preferences", "progress", "plans", "context", "coaching"]
TOPICS: tuple[str, ...] = ("body", "preferences", "progress", "plans", "context", "coaching")
MAX_NOTES = 30
MAX_NOTE_LENGTH = 300


class CoachMemoryError(DomainError):
    """A memory operation refers to a note that doesn't exist."""


class MemoryNote(ValueObject):
    """One thing the coach remembers."""

    id: int
    topic: Topic
    text: str
    since: date  # when this version was written


class MemoryOperation(ValueObject):
    """One change the coach asks for."""

    op: Literal["add", "update", "delete"]
    id: int | None = None  # the note to update or delete
    topic: Topic | None = None
    text: str | None = None


class NoteAdded(DomainEvent):
    """The coach learned something new."""

    note: MemoryNote
    by: str  # who wrote it: a coach run id, e.g. "coach-run:12"


class NoteUpdated(DomainEvent):
    """The coach corrected or refined a note; the old version is kept as history."""

    note: MemoryNote
    by: str


class NoteDeleted(DomainEvent):
    """The coach dropped a note that is no longer true or useful."""

    note_id: int
    by: str
    on: date


class NoteForgotten(DomainEvent):
    """The athlete removed a note; the coach must not bring it back without new evidence."""

    note_id: int
    text: str
    on: date


class CoachMemory(Aggregate):
    """The current notes; changed only through `apply` and `forget`."""

    notes: dict[int, MemoryNote] = {}
    next_id: int = 1

    def current(self) -> list[MemoryNote]:
        """The notes, in the order they were first written."""
        return sorted(self.notes.values(), key=lambda n: n.id)

    def apply(self, operations: list[MemoryOperation], *, by: str, today: date) -> list[str]:
        """Apply the coach's operations in order. Invalid ones are skipped (one bad operation must
        not lose a whole plan); returns why each skipped one was refused."""
        refused = []
        for operation in operations:
            text = " ".join((operation.text or "").split())[:MAX_NOTE_LENGTH]
            if operation.op == "add":
                if not text or operation.topic is None:
                    refused.append("add without a topic or text")
                elif len(self.notes) >= MAX_NOTES:
                    refused.append(f"add refused, already {MAX_NOTES} notes: {text}")
                else:
                    note = MemoryNote(id=self.next_id, topic=operation.topic, text=text, since=today)
                    self.notes = {**self.notes, note.id: note}
                    self.next_id += 1
                    self.record(NoteAdded(note=note, by=by))
            elif operation.id not in self.notes:
                refused.append(f"{operation.op} of unknown note {operation.id}")
            elif operation.op == "update":
                old = self.notes[operation.id]  # type: ignore[index]
                note = MemoryNote(id=old.id, topic=operation.topic or old.topic, text=text or old.text, since=today)
                if note.text != old.text or note.topic != old.topic:
                    self.notes = {**self.notes, note.id: note}
                    self.record(NoteUpdated(note=note, by=by))
            else:
                self.notes = {k: v for k, v in self.notes.items() if k != operation.id}
                self.record(NoteDeleted(note_id=operation.id, by=by, on=today))  # type: ignore[arg-type]
        return refused

    def forget(self, note_id: int, *, today: date) -> None:
        """The athlete removes a note."""
        note = self.notes.get(note_id)
        if note is None:
            raise CoachMemoryError("no such note")
        self.notes = {k: v for k, v in self.notes.items() if k != note_id}
        self.record(NoteForgotten(note_id=note_id, text=note.text, on=today))

"""The athlete looks at, and corrects, what the coach remembers."""

from __future__ import annotations

from sportlock.coaching.domain.memory import MemoryNote
from sportlock.coaching.domain.ports import CoachMemoryRepositoryProtocol
from sportlock.shared_kernel.base import ValueObject
from sportlock.shared_kernel.ports import ClockProtocol


class ForgetMemoryNoteCommand(ValueObject):
    """Remove one note. Handled by `ForgetMemoryNoteService`."""

    note_id: int


class ForgetMemoryNoteService:
    """Removes a note; the coach is told not to bring it back without new evidence."""

    def __init__(self, memory: CoachMemoryRepositoryProtocol, clock: ClockProtocol) -> None:
        self.memory = memory
        self.clock = clock

    def execute(self, command: ForgetMemoryNoteCommand) -> None:
        """Raises CoachMemoryError for an unknown note."""
        memory = self.memory.load()
        memory.forget(command.note_id, today=self.clock.now().date())
        self.memory.save(memory)


class GetMemoryService:
    """The current notes, for the app."""

    def __init__(self, memory: CoachMemoryRepositoryProtocol) -> None:
        self.memory = memory

    def execute(self) -> list[MemoryNote]:
        """Notes in the order they were first written."""
        return self.memory.load().current()

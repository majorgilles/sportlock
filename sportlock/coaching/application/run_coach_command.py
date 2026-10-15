"""The request to plan the next session, shared by the coach's use cases."""

from __future__ import annotations

from sportlock.shared_kernel.base import ValueObject


class RunCoachCommand(ValueObject):
    """Plan the next session. Handled by `RunCoachService`."""

    equipment: frozenset[str]
    upcoming_locks: tuple[dict, ...] = ()  # [{"start", "minutes"}] of the next scheduled locks
    rest_policy: dict = {}

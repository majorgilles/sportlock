"""Base classes for the domain's building blocks (see docs/architecture-decision-records/
2026_10_09_pydantic_domain_models.md)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, PrivateAttr


class ValueObject(BaseModel):
    """Immutable, compared by value."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Entity(BaseModel):
    """Mutable, has an identity; every assignment is validated."""

    model_config = ConfigDict(validate_assignment=True, extra="forbid")


class DomainEvent(ValueObject):
    """Something that happened in an aggregate, named in the past tense."""


class Aggregate(Entity):
    """An aggregate root: the only entry point for changing what it contains. It records the
    domain events its behaviour methods raise; the application service collects them after saving."""

    _events: list[DomainEvent] = PrivateAttr(default_factory=list)

    def record(self, event: DomainEvent) -> None:
        """Record an event raised by a behaviour method."""
        self._events.append(event)

    def pull_events(self) -> list[DomainEvent]:
        """Hand over the recorded events and forget them."""
        events, self._events = self._events, []
        return events

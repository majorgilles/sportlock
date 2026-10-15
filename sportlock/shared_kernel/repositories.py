"""The base repository protocols: every repository is made of create, get, find, update and delete.

A repository protocol extends the capabilities it needs and declares no method of its own. See
"Repositories are CRUD" in ~/.agents/AGENTS.md.
"""

from __future__ import annotations

from typing import Protocol


class CreateRepositoryProtocol[EntityT](Protocol):
    """Stores new entities."""

    def create(self, entity: EntityT) -> EntityT:
        """Store a new entity and return it as stored, with any identity the store assigned."""
        ...


class GetRepositoryProtocol[EntityT, IdT](Protocol):
    """Loads one entity by its identity."""

    def get_by_id(self, identifier: IdT) -> EntityT | None:
        """The entity with this identity, or None."""
        ...


class FindRepositoryProtocol[EntityT, FiltersT](Protocol):
    """Loads the entities matching a filters value object.

    The filters are optional fields combined with AND. A new lookup is a new field on the filters, never
    a new method. A combination with a domain meaning is a classmethod on the filters.
    """

    def find(self, filters: FiltersT) -> list[EntityT]:
        """The matching entities, in the order the filters ask for (oldest first by default)."""
        ...


class UpdateRepositoryProtocol[EntityT](Protocol):
    """Stores changes to existing entities."""

    def update(self, entity: EntityT) -> EntityT:
        """Store the entity's new state and return it as stored."""
        ...


class DeleteRepositoryProtocol[IdT](Protocol):
    """Removes entities."""

    def delete(self, identifier: IdT) -> None:
        """Remove the entity with this identity; removing a missing one is not an error."""
        ...


class GetSingletonRepositoryProtocol[EntityT](Protocol):
    """Loads the one stored value of its kind, such as the settings."""

    def get(self) -> EntityT | None:
        """The stored value, or None when there is none yet."""
        ...


class UpdateSingletonRepositoryProtocol[EntityT](Protocol):
    """Replaces the one stored value of its kind."""

    def update(self, entity: EntityT) -> EntityT:
        """Store the value, creating it the first time, and return it as stored."""
        ...

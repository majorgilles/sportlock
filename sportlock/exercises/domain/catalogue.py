"""The exercise catalogue: every exercise, grouped in progression chains (easiest → hardest).

The seed data says what exists; the built details (instructions, picture) come from the books
through `ExerciseDetailsRepositoryProtocol`.
"""

from __future__ import annotations

from sportlock.shared_kernel.base import ValueObject
from sportlock.shared_kernel.targets import Kind, Sides


class Exercise(ValueObject):
    """One exercise and its place in its chain."""

    id: str
    name: str
    kind: Kind
    chain: str
    pattern: str
    step: int  # 1-based position in the chain
    chain_ids: tuple[str, ...]
    easier: str | None
    harder: str | None
    equipment: frozenset[str] = frozenset()
    sides: Sides | None = None
    aliases: tuple[str, ...] = ()
    cues: tuple[str, ...] = ()  # seed cues, used until the details are built
    fedb: str | None = None  # free-exercise-db name for a fallback photo

    def available_with(self, equipment: set[str] | frozenset[str]) -> bool:
        """Whether the user has everything this exercise needs."""
        return self.equipment <= equipment


class ExerciseDetails(ValueObject):
    """Instructions and picture built from the user's books."""

    steps: tuple[str, ...] = ()
    cues: tuple[str, ...] = ()
    mistakes: tuple[str, ...] = ()
    breathing: str = ""
    sources: tuple[str, ...] = ()
    image: str = ""  # absolute path, or "" without a picture
    image_source: str = ""
    built: bool = False


class Catalogue(ValueObject):
    """All exercises by id, in seed order (chains in order, each easiest first)."""

    exercises: dict[str, Exercise] = {}

    @classmethod
    def from_seed(cls, seed: dict) -> Catalogue:
        """Build from the seed JSON: {"chains": {chain: {"pattern", "exercises": [...]}}}."""
        exercises = {}
        for chain_id, chain in seed["chains"].items():
            ids = tuple(e["id"] for e in chain["exercises"])
            for index, entry in enumerate(chain["exercises"]):
                exercises[entry["id"]] = Exercise(
                    id=entry["id"],
                    name=entry["name"],
                    kind=entry["kind"],
                    chain=chain_id,
                    pattern=chain["pattern"],
                    step=index + 1,
                    chain_ids=ids,
                    easier=ids[index - 1] if index > 0 else None,
                    harder=ids[index + 1] if index + 1 < len(ids) else None,
                    equipment=frozenset(entry.get("equipment", [])),
                    sides=entry.get("sides"),
                    aliases=tuple(entry.get("aliases", [])),
                    cues=tuple(entry.get("cues", [])),
                    fedb=entry.get("fedb"),
                )
        return cls(exercises=exercises)

    def __contains__(self, exercise_id: object) -> bool:
        return exercise_id in self.exercises

    def get(self, exercise_id: str) -> Exercise:
        """The exercise; raises KeyError for an unknown id."""
        return self.exercises[exercise_id]

    def find(self, exercise_id: str) -> Exercise | None:
        """The exercise, or None for an unknown id (e.g. one removed from the seed)."""
        return self.exercises.get(exercise_id)

    def name(self, exercise_id: str) -> str:
        """Display name, falling back to the id for exercises no longer in the catalogue."""
        exercise = self.find(exercise_id)
        return exercise.name if exercise else exercise_id

    def all(self) -> list[Exercise]:
        """Every exercise in seed order."""
        return list(self.exercises.values())

    def easiest_available(self, exercise_id: str, equipment: set[str] | frozenset[str]) -> str | None:
        """The exercise itself, or the nearest easier one on its chain that needs no missing equipment."""
        current: str | None = exercise_id
        while current:
            exercise = self.get(current)
            if exercise.available_with(equipment):
                return current
            current = exercise.easier
        return None

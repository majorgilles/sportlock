"""What the exercise subdomain needs from the outside world."""

from __future__ import annotations

from typing import Protocol

from sportlock.exercises.domain.catalogue import ExerciseDetails


class ExerciseDetailsRepositoryProtocol(Protocol):
    """Built instructions and pictures, per exercise."""

    def get(self, exercise_id: str) -> ExerciseDetails:
        """The built details, or empty details when the exercise isn't built yet."""
        ...

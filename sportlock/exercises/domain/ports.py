"""What the exercise subdomain needs from the outside world."""

from __future__ import annotations

from typing import Protocol

from sportlock.exercises.domain.catalogue import ExerciseDetails
from sportlock.shared_kernel.repositories import GetRepositoryProtocol


class ExerciseDetailsRepositoryProtocol(GetRepositoryProtocol[ExerciseDetails, str], Protocol):
    """Built instructions and pictures, per exercise id.

    `get_by_id` gives empty details for an exercise that is not built yet.
    """

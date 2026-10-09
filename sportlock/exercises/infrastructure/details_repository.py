"""Built exercise details, one folder per exercise: <library>/<id>/exercise.json plus its picture."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import override

from sportlock.exercises.domain.catalogue import Catalogue, ExerciseDetails
from sportlock.exercises.domain.ports import ExerciseDetailsRepositoryProtocol
from sportlock.shared_kernel.infrastructure.database import DATA_DIR

LIBRARY_DIR = DATA_DIR / "library"


def load_catalogue() -> Catalogue:
    """The catalogue from the packaged seed data."""
    seed = json.loads(resources.files("sportlock.exercises").joinpath("data/exercises.json").read_text())
    return Catalogue.from_seed(seed)


class FileExerciseDetailsRepository(ExerciseDetailsRepositoryProtocol):
    """Reads what `sportlock library build` wrote."""

    def __init__(self, root: Path = LIBRARY_DIR) -> None:
        self.root = root

    @override
    def get(self, exercise_id: str) -> ExerciseDetails:
        path = self.root / exercise_id / "exercise.json"
        try:
            data = json.loads(path.read_text())
        except OSError, ValueError:
            return ExerciseDetails()
        image = data.get("image")
        return ExerciseDetails(
            steps=data.get("steps") or (),
            cues=data.get("cues") or (),
            mistakes=data.get("mistakes") or (),
            breathing=data.get("breathing") or "",
            sources=data.get("sources") or (),
            image=str(self.root / exercise_id / image) if image else "",
            image_source=data.get("image_source") or "",
            built=bool(data.get("built_at")),
        )

"""The active session as the lock screen shows it (part of the state-file contract with
locks/ui/lock_screen/shell.qml)."""

from __future__ import annotations

from datetime import datetime

from sportlock.exercises.domain.catalogue import Catalogue
from sportlock.exercises.domain.ports import ExerciseDetailsRepositoryProtocol
from sportlock.progression.domain.ladders import laddered
from sportlock.shared_kernel.time import epoch_ms
from sportlock.training.domain.repositories import TrainingHistoryProtocol, TrainingSessionRepositoryProtocol


def _ms(moment: datetime | None) -> int | None:
    return epoch_ms(moment) if moment else None


class TrainingSnapshotService:
    """Builds the lock screen's view of the active session."""

    def __init__(
        self,
        sessions: TrainingSessionRepositoryProtocol,
        history: TrainingHistoryProtocol,
        catalogue: Catalogue,
        details: ExerciseDetailsRepositoryProtocol,
    ) -> None:
        self.sessions = sessions
        self.history = history
        self.catalogue = catalogue
        self.details = details

    def execute(self) -> dict | None:
        """None without an active session."""
        session = self.sessions.active()
        if session is None:
            return None
        exercises = []
        for e in session.exercises:
            spec = self.catalogue.find(e.exercise)
            details = self.details.get(e.exercise)
            exercises.append(
                {
                    "name": e.name,
                    "pattern": e.pattern,
                    "kind": e.kind,
                    "target": e.target.to_dict(),
                    "cues": list(details.cues or (spec.cues if spec else ())),
                    "steps": list(details.steps),
                    "mistakes": list(details.mistakes),
                    "breathing": details.breathing,
                    "sources": list(details.sources),
                    "image": details.image,
                    "image_source": details.image_source,
                    "easier_name": self.catalogue.name(spec.easier) if spec and spec.easier else "",
                    "harder_name": self.catalogue.name(spec.harder) if spec and spec.harder else "",
                    "has_easier": bool(spec and spec.easier and laddered(spec.chain)),
                    "status": e.status,
                    "rpe": e.rpe,
                    "sides": spec.sides or "" if spec else "",
                    "times_done": self.history.times_done(e.exercise, session.id),
                    "sets": [{"reps": s.reps, "seconds": s.seconds, "load_kg": s.load_kg} for s in e.sets],
                }
            )
        pending = None
        if session.phase == "logging" and session.set_started_at and session.set_ended_at:
            pending = (session.set_ended_at - session.set_started_at).total_seconds()
        return {
            "id": session.id,
            "title": session.title,
            "day_type": session.day_type,
            "kind": session.kind,
            "note": session.coach_note,
            "source": session.plan_source,
            "phase": session.phase,
            "current": session.current,
            "exercises": exercises,
            "set_started_at": _ms(session.set_started_at),
            "rest_until": _ms(session.rest_until),
            "pending_seconds": pending,
        }

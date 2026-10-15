"""What the coach sees when it plans a session or reviews the trainee."""

from __future__ import annotations

from datetime import datetime, timedelta

from sportlock.athlete.domain.profile import ProfileRepositoryProtocol
from sportlock.coaching.application.run_coach_command import RunCoachCommand
from sportlock.coaching.domain.ports import CoachMemoryRepositoryProtocol, ReplacementAnswerRepositoryProtocol
from sportlock.exercises.domain.catalogue import Catalogue
from sportlock.locks.domain.repositories import LockEventRepositoryProtocol
from sportlock.progression.domain.ladders import LadderRepositoryProtocol, positions
from sportlock.recovery.domain.policy import load_summary, rest_days_in_a_row
from sportlock.shared_kernel.time import iso
from sportlock.training.domain.repositories import TrainingHistoryProtocol

HISTORY_DAYS = 28
HISTORY_SESSIONS = 12
FORGOTTEN_SHOWN = 30
ANSWERS_SHOWN = 10


class RecallCoachMemoryService:
    """What the coach remembers: its notes, what the athlete made it forget, and answers to replacements."""

    def __init__(
        self,
        memory: CoachMemoryRepositoryProtocol,
        answers: ReplacementAnswerRepositoryProtocol,
        catalogue: Catalogue,
    ) -> None:
        self.memory = memory
        self.answers = answers
        self.catalogue = catalogue

    def execute(self) -> dict:
        """The memory part of the coach's context."""
        memory = self.memory.load()
        return {
            "coach_memory": [
                {"id": n.id, "topic": n.topic, "note": n.text, "since": n.since.isoformat()} for n in memory.current()
            ],
            "forgotten_by_user": self.memory.forgotten(FORGOTTEN_SHOWN),
            "answers_to_replacements": [
                {
                    "on": a.answered_at[:10],
                    "chain": a.chain,
                    "replaced": self.catalogue.name(a.from_exercise),
                    "by": self.catalogue.name(a.to_exercise),
                    "answer": "agreed" if a.agreed else "kept the replaced exercise",
                    **({"note": a.note} if a.note else {}),
                }
                for a in self.answers.recent(ANSWERS_SHOWN)
            ],
        }


class BuildCoachContextService:
    """Everything the coach sees, in the order of the prompt's schema."""

    def __init__(
        self,
        profiles: ProfileRepositoryProtocol,
        ladders: LadderRepositoryProtocol,
        history: TrainingHistoryProtocol,
        lock_events: LockEventRepositoryProtocol,
        catalogue: Catalogue,
        recall_memory: RecallCoachMemoryService,
    ) -> None:
        self.profiles = profiles
        self.ladders = ladders
        self.history = history
        self.lock_events = lock_events
        self.catalogue = catalogue
        self.recall_memory = recall_memory

    def _ladders(self) -> list[dict]:
        ladders = []
        for position in positions(self.ladders.saved()):
            exercise = self.catalogue.get(position.exercise)
            ladders.append(
                {
                    "chain": position.chain,
                    "exercise": position.exercise,
                    "name": exercise.name,
                    "step": f"{exercise.step}/{len(exercise.chain_ids)}",
                    "target": position.target.to_dict(),
                    "why": position.reason,
                }
            )
        return ladders

    def _catalogue(self, equipment: frozenset[str]) -> list[dict]:
        return [
            {
                "id": e.id,
                "name": e.name,
                "chain": e.chain,
                "step": e.step,
                "kind": e.kind,
                **({"sides": e.sides} if e.sides else {}),
                "equipment": sorted(e.equipment),
                "available": e.available_with(equipment),
            }
            for e in self.catalogue.all()
        ]

    def execute(self, now: datetime, command: RunCoachCommand) -> dict:
        """The coach's context at this time."""
        profile = self.profiles.get()
        ladders = self._ladders()
        recent = self.history.recent_details((now - timedelta(days=HISTORY_DAYS)).date(), HISTORY_SESSIONS)
        proposals = self.ladders.moves_for(self.history.last_counted_id() or 0) if recent else []
        week = self.history.credited_since(now - timedelta(days=7))
        catalogue = self._catalogue(command.equipment)
        last_hard = self.history.last_hard()
        return {
            "now": iso(now),
            "profile": {k: v for k, v in profile.to_dict().items() if k != "updated_at"} if profile else {},
            **self.recall_memory.execute(),
            "equipment": sorted(command.equipment),
            "last_hard_session": iso(last_hard) if last_hard else None,
            "ladders": ladders,
            "rule_proposals_from_last_session": proposals,
            "load": load_summary(week, rest_days_in_a_row(self.lock_events.rest_days(), now.date()), now),
            "upcoming_locks": list(command.upcoming_locks),
            "rest_policy": command.rest_policy,
            "recent_sessions": recent,
            "catalogue": catalogue,
        }

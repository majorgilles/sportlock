"""What the coach sees when it plans a session or reviews the trainee."""

from __future__ import annotations

from datetime import datetime, timedelta

from sportlock.athlete.domain.profile import ProfileRepositoryProtocol
from sportlock.coaching.application.run_coach_command import RunCoachCommand
from sportlock.coaching.domain.ports import (
    CoachMemoryRepositoryProtocol,
    MemoryNoteVersionFilters,
    MemoryNoteVersionRepositoryProtocol,
    ReplacementAnswerFilters,
    ReplacementAnswerRepositoryProtocol,
)
from sportlock.exercises.domain.catalogue import Catalogue
from sportlock.locks.domain.locks import rest_days
from sportlock.locks.domain.repositories import LockEventFilters, LockEventRepositoryProtocol
from sportlock.progression.domain.ladders import (
    LadderMoveRecordFilters,
    LadderMoveRecordRepositoryProtocol,
    LadderPositionFilters,
    LadderPositionRepositoryProtocol,
    positions,
)
from sportlock.recovery.domain.policy import load_summary, rest_days_in_a_row
from sportlock.shared_kernel.time import iso
from sportlock.training.application.training_history import TrainingHistory

HISTORY_DAYS = 28
HISTORY_SESSIONS = 12
FORGOTTEN_SHOWN = 30
ANSWERS_SHOWN = 10


class RecallCoachMemoryService:
    """What the coach remembers: its notes, what the athlete made it forget, and answers to replacements."""

    def __init__(
        self,
        coach_memory_repository: CoachMemoryRepositoryProtocol,
        memory_note_version_repository: MemoryNoteVersionRepositoryProtocol,
        replacement_answer_repository: ReplacementAnswerRepositoryProtocol,
        catalogue: Catalogue,
    ) -> None:
        self.coach_memory_repository = coach_memory_repository
        self.memory_note_version_repository = memory_note_version_repository
        self.replacement_answer_repository = replacement_answer_repository
        self.catalogue = catalogue

    def execute(self) -> dict:
        """The memory part of the coach's context."""
        memory = self.coach_memory_repository.get()
        return {
            "coach_memory": [
                {"id": n.id, "topic": n.topic, "note": n.text, "since": n.since.isoformat()} for n in memory.current()
            ],
            "forgotten_by_user": [
                version.text
                for version in self.memory_note_version_repository.find(
                    MemoryNoteVersionFilters(end_reason="forgotten", latest=FORGOTTEN_SHOWN)
                )
            ],
            "answers_to_replacements": [
                {
                    "on": a.answered_at[:10],
                    "chain": a.chain,
                    "replaced": self.catalogue.name(a.from_exercise),
                    "by": self.catalogue.name(a.to_exercise),
                    "answer": "agreed" if a.agreed else "kept the replaced exercise",
                    **({"note": a.note} if a.note else {}),
                }
                for a in self.replacement_answer_repository.find(ReplacementAnswerFilters(latest=ANSWERS_SHOWN))
            ],
        }


class DescribeLaddersForCoachService:
    """The ladders as the coach sees them: every chain's position, and the moves the last session made."""

    def __init__(
        self,
        ladder_position_repository: LadderPositionRepositoryProtocol,
        ladder_move_record_repository: LadderMoveRecordRepositoryProtocol,
        catalogue: Catalogue,
    ) -> None:
        self.ladder_position_repository = ladder_position_repository
        self.ladder_move_record_repository = ladder_move_record_repository
        self.catalogue = catalogue

    def _ladders(self) -> list[dict]:
        ladders = []
        for position in positions(self.ladder_position_repository.find(LadderPositionFilters())):
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

    def execute(self, last_session: int | None) -> dict:
        """The "ladders" part of the coach's context, then the rules' proposals from `last_session` (if any)."""
        proposals = (
            [
                record.to_dict()
                for record in self.ladder_move_record_repository.find(LadderMoveRecordFilters(session_id=last_session))
            ]
            if last_session is not None
            else []
        )
        return {"ladders": self._ladders(), "rule_proposals_from_last_session": proposals}


class BuildCoachContextService:
    """Everything the coach sees, in the order of the prompt's schema."""

    def __init__(
        self,
        profile_repository: ProfileRepositoryProtocol,
        training_history: TrainingHistory,
        lock_event_repository: LockEventRepositoryProtocol,
        catalogue: Catalogue,
        recall_coach_memory_service: RecallCoachMemoryService,
        describe_ladders_for_coach_service: DescribeLaddersForCoachService,
    ) -> None:
        self.profile_repository = profile_repository
        self.training_history = training_history
        self.lock_event_repository = lock_event_repository
        self.catalogue = catalogue
        self.recall_coach_memory_service = recall_coach_memory_service
        self.describe_ladders_for_coach_service = describe_ladders_for_coach_service

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
        profile = self.profile_repository.get()
        recent = self.training_history.recent_details((now - timedelta(days=HISTORY_DAYS)).date(), HISTORY_SESSIONS)
        ladders = self.describe_ladders_for_coach_service.execute(
            (self.training_history.last_counted_id() or 0) if recent else None
        )
        week = self.training_history.credited_since(now - timedelta(days=7))
        catalogue = self._catalogue(command.equipment)
        last_hard = self.training_history.last_hard()
        return {
            "now": iso(now),
            "profile": {k: v for k, v in profile.to_dict().items() if k != "updated_at"} if profile else {},
            **self.recall_coach_memory_service.execute(),
            "equipment": sorted(command.equipment),
            "last_hard_session": iso(last_hard) if last_hard else None,
            **ladders,
            "load": load_summary(
                week,
                rest_days_in_a_row(
                    rest_days(self.lock_event_repository.find(LockEventFilters(outcomes=("rest",)))), now.date()
                ),
                now,
            ),
            "upcoming_locks": list(command.upcoming_locks),
            "rest_policy": command.rest_policy,
            "recent_sessions": recent,
            "catalogue": catalogue,
        }

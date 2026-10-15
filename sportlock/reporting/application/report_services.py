"""The Report tab: the trainee's dossier, stats, last session, where to improve and a journal of every session.

And the coach's review that writes the dossier and the advice.
"""

from __future__ import annotations

from datetime import date, datetime

from sportlock.athlete.domain.profile import ProfileRepositoryProtocol
from sportlock.coaching.application.coach_context_service import BuildCoachContextService
from sportlock.coaching.application.run_coach_command import RunCoachCommand
from sportlock.coaching.domain.memory import TOPICS
from sportlock.coaching.domain.ports import CoachMemoryRepositoryProtocol, CoachProtocol, CoachReviewRepositoryProtocol
from sportlock.coaching.domain.review import CoachReview, parse_review
from sportlock.exercises.domain.catalogue import Catalogue
from sportlock.locks.domain.repositories import LockEventRepositoryProtocol
from sportlock.progression.domain.ladders import START, LadderRepositoryProtocol, positions
from sportlock.reporting.domain.report import (
    ExerciseRecord,
    LadderLevel,
    SessionRecord,
    SetRecord,
    TrainingStats,
    observations,
    personal_bests,
    training_stats,
)
from sportlock.shared_kernel.errors import DomainError
from sportlock.shared_kernel.ports import ClockProtocol
from sportlock.shared_kernel.time import iso
from sportlock.training.domain.repositories import TrainingHistoryProtocol

FIRST_DAY = date(2000, 1, 1)
LOCKS_LOOKED_AT = 500


class ReviewError(DomainError):
    """There is nothing to review yet."""


def _moment(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


def _record(row: dict) -> SessionRecord:
    return SessionRecord(
        id=row["id"],
        day=date.fromisoformat(row["day"]),
        started_at=_moment(row.get("started_at")),
        finished_at=_moment(row.get("finished_at")),
        kind=row["kind"],
        status=row.get("status") or "finished",
        title=row.get("title") or "",
        day_type=row.get("day_type") or "",
        rpe=row.get("rpe"),
        notes=row.get("notes") or "",
        exercises=tuple(
            ExerciseRecord(
                exercise=e.get("exercise", ""),
                name=e["name"],
                kind=e.get("kind", ""),
                status=e["status"],
                rpe=e.get("rpe"),
                note=e.get("note") or "",
                skip_reason=e.get("skip_reason") or "",
                sets=tuple(SetRecord(**s) for s in e.get("sets", [])),
            )
            for e in row.get("exercises", [])
        ),
    )


class TrainingRecords:
    """Read-only questions about the trainee's history, shared by the report and the coach's review.

    It is a query helper rather than a use case, like `CoachPlanFreshness`
    (see 2026_10_09_application_services_execute.md).
    """

    def __init__(
        self,
        history: TrainingHistoryProtocol,
        ladders: LadderRepositoryProtocol,
        lock_events: LockEventRepositoryProtocol,
        catalogue: Catalogue,
        clock: ClockProtocol,
    ) -> None:
        self.history = history
        self.ladders = ladders
        self.lock_events = lock_events
        self.catalogue = catalogue
        self.clock = clock

    def sessions(self) -> list[SessionRecord]:
        """Every counted session, oldest first."""
        return [_record(r) for r in self.history.between(FIRST_DAY, self.clock.now().date())]

    def levels(self, sessions: list[SessionRecord]) -> list[LadderLevel]:
        """The ladder position of every chain trained at least once."""
        found = [self.catalogue.find(e.exercise) for s in sessions for e in s.exercises]
        trained = {exercise.chain for exercise in found if exercise}
        levels = []
        for position in positions(self.ladders.saved()):
            if position.chain not in trained:
                continue
            exercise = self.catalogue.get(position.exercise)
            levels.append(
                LadderLevel(
                    chain=position.chain,
                    exercise=exercise.id,
                    name=exercise.name,
                    step=exercise.step,
                    steps=len(exercise.chain_ids),
                    start_step=self.catalogue.get(START[position.chain][0]).step,
                )
            )
        return levels

    def stats(self, sessions: list[SessionRecord]) -> TrainingStats:
        """The headline numbers."""
        locks = sorted(
            (lock for lock in self.lock_events.recent(LOCKS_LOOKED_AT) if lock["key"][:1].isdigit()),
            key=lambda lock: lock["start"],
        )
        return training_stats(sessions, [lock["outcome"] for lock in locks], self.clock.now().date())

    def laddered_exercises(self) -> set[str]:
        """Every exercise on one of the ladders."""
        return {e for chain in START for e in self.catalogue.get(START[chain][0]).chain_ids}

    def journal_entry(self, session: SessionRecord, review: CoachReview | None) -> dict:
        """One session as the journal shows it, with its ladder changes and the coach's answer."""
        changes = [
            {
                "chain": m["chain"],
                "from": self.catalogue.name(m["from"]),
                "to": self.catalogue.name(m["to"]),
                "rule": m["rule"],
                "reason": m["reason"],
                "by": {"agent": "coach", "athlete": "you"}.get(m.get("decided_by", ""), "rules"),
            }
            for m in self.ladders.moves_for(session.id)
            if m["rule"] != "hold"
        ]
        return {
            "id": session.id,
            "date": session.day.isoformat(),
            "title": session.title,
            "kind": session.kind,
            "day_type": session.day_type,
            "status": session.status,
            "minutes": session.minutes,
            "effort": session.rpe,
            "notes": session.notes,
            "exercises": [e.model_dump() for e in session.exercises if e.status != "swapped"],
            "changes": changes,
            "coach": review
            and {
                "last_session": review.last_session,
                "recommendations": [r.model_dump() for r in review.recommendations],
            },
        }


class BuildReportService:
    """Everything the Report tab shows, from the data and the coach's latest review."""

    def __init__(
        self,
        records: TrainingRecords,
        profiles: ProfileRepositoryProtocol,
        memory: CoachMemoryRepositoryProtocol,
        reviews: CoachReviewRepositoryProtocol,
        clock: ClockProtocol,
    ) -> None:
        self.records = records
        self.profiles = profiles
        self.memory = memory
        self.reviews = reviews
        self.clock = clock

    def execute(self) -> dict:
        """The report (part of the socket API's contract with the app window)."""
        sessions = self.records.sessions()
        stats = self.records.stats(sessions)
        levels = self.records.levels(sessions)
        laddered = self.records.laddered_exercises()
        reviews = self.reviews.all()
        latest = self.reviews.latest()
        profile = self.profiles.get()
        journal = [self.records.journal_entry(s, reviews.get(s.id)) for s in reversed(sessions)]
        memory = self.memory.load().current()
        return {
            "generated_at": iso(self.clock.now()),
            "profile": profile.to_dict() if profile else None,
            "dossier": latest
            and {"text": latest.dossier, "written_at": latest.written_at, "session": latest.session_id},
            "memory": [
                {"topic": n.topic, "note": n.text, "since": n.since.isoformat()}
                for n in sorted(memory, key=lambda n: (TOPICS.index(n.topic), n.id))
            ],
            "ladders": [{**level.model_dump(), "moved": level.moved} for level in levels],
            "stats": stats.model_dump(mode="json"),
            "bests": [b.model_dump(mode="json") for b in personal_bests(sessions, laddered)],
            "last_session": journal[0] if journal else None,
            "improve": {
                "coach": [i.model_dump() for i in latest.improve] if latest else [],
                "numbers": [o.model_dump() for o in observations(stats, levels)],
            },
            "journal": journal,
        }


class WriteCoachReviewService:
    """Asks the coach to review the trainee after their latest session, with the report's numbers."""

    def __init__(
        self,
        coach: CoachProtocol,
        build_context: BuildCoachContextService,
        records: TrainingRecords,
        reviews: CoachReviewRepositoryProtocol,
        history: TrainingHistoryProtocol,
        clock: ClockProtocol,
    ) -> None:
        self.coach = coach
        self.build_context = build_context
        self.records = records
        self.reviews = reviews
        self.history = history
        self.clock = clock

    def execute(self, command: RunCoachCommand) -> CoachReview:
        """Raises ReviewError before the first session, CoachUnavailableError or CoachOutputError."""
        session_id = self.history.last_counted_id()
        if session_id is None:
            raise ReviewError("train once first: the coach reviews your sessions")
        now = self.clock.now()
        sessions = self.records.sessions()
        context = {
            **self.build_context.execute(now, command),
            "stats": self.records.stats(sessions).model_dump(mode="json"),
            "ladder_levels": [
                {**level.model_dump(), "moved_since_start": level.moved} for level in self.records.levels(sessions)
            ],
        }
        review = parse_review(self.coach.write_review(context), session_id=session_id, written_at=iso(now))
        earlier = self.reviews.get(session_id)
        if earlier is not None:
            review = review.with_recommendations(earlier.recommendations)
        self.reviews.save(review)
        return review

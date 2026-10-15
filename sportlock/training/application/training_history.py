"""TrainingHistory: read-only questions about past sessions, shared by many use cases.

It is a query helper rather than a use case, like `CoachPlanFreshness` (see
2026_10_09_application_services_execute.md). It reads the training read-model repositories and computes the
answers (trained days, latest finishes, counts) from what they find.
"""

from __future__ import annotations

from datetime import date, datetime

from sportlock.training.domain.repositories import (
    CompletedExerciseFilters,
    CompletedExerciseRepositoryProtocol,
    CountedSessionFilters,
    CountedSessionRepositoryProtocol,
    ExerciseTiming,
    ExerciseTimingFilters,
    ExerciseTimingRepositoryProtocol,
    SessionSummary,
    SessionSummaryFilters,
    SessionSummaryRepositoryProtocol,
    TransitionGapFilters,
    TransitionGapRepositoryProtocol,
)


def _latest_finish(summaries: list[SessionSummary]) -> datetime | None:
    return max((summary.finished_at for summary in summaries), default=None)


class TrainingHistory:
    """Past sessions: credited ones for load and recovery, counted ones for the coach, calendar and report."""

    def __init__(
        self,
        session_summary_repository: SessionSummaryRepositoryProtocol,
        exercise_timing_repository: ExerciseTimingRepositoryProtocol,
        transition_gap_repository: TransitionGapRepositoryProtocol,
        completed_exercise_repository: CompletedExerciseRepositoryProtocol,
        counted_session_repository: CountedSessionRepositoryProtocol,
    ) -> None:
        self.session_summary_repository = session_summary_repository
        self.exercise_timing_repository = exercise_timing_repository
        self.transition_gap_repository = transition_gap_repository
        self.completed_exercise_repository = completed_exercise_repository
        self.counted_session_repository = counted_session_repository

    def trained_days(self) -> set[date]:
        """Days with a credited session."""
        return {summary.day for summary in self.session_summary_repository.find(SessionSummaryFilters())}

    def credited_since(self, since: datetime) -> list[SessionSummary]:
        """Credited sessions finished since `since`, oldest first."""
        return self.session_summary_repository.find(SessionSummaryFilters(finished_since=since))

    def last_credited(self) -> datetime | None:
        """When the last credited session finished."""
        return _latest_finish(self.session_summary_repository.find(SessionSummaryFilters()))

    def last_hard(self) -> datetime | None:
        """When the last credited hard session finished."""
        return _latest_finish(self.session_summary_repository.find(SessionSummaryFilters(day_type="hard")))

    def exercise_timings(self, sessions: int) -> list[ExerciseTiming]:
        """Done exercises of the last `sessions` sessions, with their real duration."""
        return self.exercise_timing_repository.find(ExerciseTimingFilters(last_sessions=sessions))

    def transition_gaps(self, sessions: int) -> list[float]:
        """Seconds between the lock starting and the first exercise, and between exercises."""
        return [
            gap.seconds for gap in self.transition_gap_repository.find(TransitionGapFilters(last_sessions=sessions))
        ]

    def times_done(self, exercise: str, excluding_session: int | None) -> int:
        """How often the exercise was done before."""
        filters = CompletedExerciseFilters(exercise=exercise, excluding_session=excluding_session)
        return len(self.completed_exercise_repository.find(filters))

    def recent_details(self, since: date, limit: int) -> list[dict]:
        """Recent counted sessions in full (exercises, sets, efforts, notes), newest first."""
        filters = CountedSessionFilters(first_day=since, newest_first=True, latest=limit)
        return [session.to_coach_dict() for session in self.counted_session_repository.find(filters)]

    def between(self, first: date, last: date) -> list[dict]:
        """Counted sessions between two days, with their exercises, for the calendar."""
        filters = CountedSessionFilters(first_day=first, last_day=last)
        return [session.to_calendar_dict() for session in self.counted_session_repository.find(filters)]

    def last_counted_id(self) -> int | None:
        """Id of the latest counted, closed session."""
        latest = self.counted_session_repository.find(CountedSessionFilters(newest_first=True, latest=1))
        return latest[0].id if latest else None

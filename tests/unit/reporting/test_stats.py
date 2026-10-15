from datetime import date, datetime

from sportlock.reporting.domain.report import (
    ExerciseRecord,
    LadderLevel,
    SessionRecord,
    SetRecord,
    observations,
    personal_bests,
    training_stats,
)

TODAY = date(2026, 10, 13)


def _session(session_id, day, minutes=20, **overrides):
    """A session record; `overrides` replace the defaults for rpe, status, day_type and exercises."""
    fields = {"rpe": 5, "status": "finished", "day_type": "hard", "exercises": (), **overrides}
    start = datetime.combine(day, datetime.min.time()).replace(hour=18)
    return SessionRecord(
        id=session_id,
        day=day,
        started_at=start,
        finished_at=start.replace(minute=minutes),
        kind="scheduled",
        status=fields["status"],
        day_type=fields["day_type"],
        rpe=fields["rpe"],
        exercises=tuple(fields["exercises"]),
    )


def _reps(exercise, *reps, status="done", skip_reason=""):
    return ExerciseRecord(
        exercise=exercise,
        name=exercise.replace("-", " ").capitalize(),
        kind="reps",
        status=status,
        skip_reason=skip_reason,
        sets=tuple(SetRecord(reps=r) for r in reps),
    )


def test_training_stats__finished_sessions__totals_windows_and_weeks():
    sessions = [
        _session(1, date(2026, 9, 1), minutes=30, rpe=4),
        _session(2, date(2026, 10, 12), minutes=25, rpe=6, day_type="light"),
        _session(3, date(2026, 10, 13), minutes=15, rpe=None, status="abandoned"),
    ]
    stats = training_stats(sessions, [], TODAY)
    assert (stats.sessions, stats.minutes, stats.first_day) == (2, 55, date(2026, 9, 1))
    assert (stats.last_7_days.sessions, stats.last_7_days.minutes) == (1, 25)
    assert (stats.hard_sessions, stats.light_sessions, stats.average_effort) == (1, 1, 5.0)
    assert [(w.start, w.minutes) for w in stats.weeks[-2:]] == [(date(2026, 10, 5), 0), (date(2026, 10, 12), 25)]


def test_training_stats__lock_outcomes__counted_and_streak_ignores_rest_days():
    outcomes = ["completed", "override", "completed", "rest", "completed", None]
    stats = training_stats([], outcomes, TODAY)
    assert stats.locks == {"completed": 3, "override": 1, "expired": 0, "rest": 1}
    assert stats.locks_kept_in_a_row == 2


def test_training_stats__repeated_skip_reasons__listed_once():
    skipped = [_reps("dead-bug", status="skipped", skip_reason="test") for _ in range(3)]
    stats = training_stats([_session(1, TODAY, exercises=skipped)], [], TODAY)
    assert (stats.exercises_skipped, stats.skip_reasons) == (3, ("test",))


def test_personal_bests__several_sessions__best_set_and_the_day_it_was_reached():
    sessions = [
        _session(1, date(2026, 10, 5), exercises=[_reps("crunch", 12, 15)]),
        _session(2, date(2026, 10, 8), exercises=[_reps("crunch", 15, 14), _reps("cat-cow", 20)]),
    ]
    [best] = personal_bests(sessions, {"crunch"})
    assert (best.name, best.reps, best.day) == ("Crunch", 15, date(2026, 10, 5))


def test_observations__easy_sessions_and_unmoved_ladders__both_pointed_out():
    sessions = [_session(i, date(2026, 10, i), rpe=3) for i in range(1, 8)]
    stats = training_stats(sessions, [], TODAY)
    levels = [
        LadderLevel(chain="squat", exercise="step-up", name="Step-up", step=4, steps=12, start_step=3),
        LadderLevel(chain="hip", exercise="x", name="Side leg raise", step=1, steps=3, start_step=1),
        LadderLevel(chain="core", exercise="y", name="Knee plank", step=2, steps=10, start_step=3),
    ]
    found = observations(stats, levels)
    assert [o.area for o in found] == ["Effort", "Patterns that haven't moved"]
    assert "Side leg raise" in found[1].advice and "Knee plank" not in found[1].advice

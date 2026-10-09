from datetime import date, datetime, timedelta

from sportlock.recovery.domain.policy import (
    DEFAULT_PACE,
    DEFAULT_TRANSITION,
    CoachAdvice,
    Policy,
    RecoveryState,
    decide_lock,
    load_summary,
    pace_factor,
    rest_days_in_a_row,
    transition_seconds,
)
from sportlock.shared_kernel.targets import Target
from sportlock.training.domain.repositories import ExerciseTiming, SessionSummary

NOW = datetime(2026, 10, 6, 18, 0)  # Tuesday
REST = CoachAdvice(mode="rest", reason="45 min at effort 8 yesterday")


def _state(last_credited=None, last_hard=None, sessions=0, rest_in_a_row=0):
    return RecoveryState(
        last_credited=last_credited,
        last_hard=last_hard,
        sessions_last_7_days=sessions,
        rest_days_in_a_row=rest_in_a_row,
    )


def _decide(state, advice=None, policy=Policy(), minutes=30):
    return decide_lock(state, policy, now=NOW, window_minutes=minutes, advice=advice)


def test_decide_lock__no_history__hard():
    assert _decide(_state()).mode == "hard"


def test_decide_lock__hard_session_yesterday__default_length_recovery():
    yesterday = NOW - timedelta(hours=24)
    decision = _decide(_state(yesterday, yesterday, 1))
    assert (decision.mode, decision.minutes, decision.reason) == ("recovery", 15, "Hard session 24 h ago")


def test_decide_lock__coach_asks_rest_and_guardrails_allow__rest():
    decision = _decide(_state(NOW - timedelta(hours=25), NOW - timedelta(hours=25), 3), REST)
    assert (decision.mode, decision.minutes, decision.reason) == ("rest", 0, "45 min at effort 8 yesterday")


def test_decide_lock__rest_with_too_few_sessions_this_week__shorter_recovery():
    advice = CoachAdvice(mode="rest", recovery_minutes=10, reason="tired")
    decision = _decide(_state(NOW - timedelta(hours=20), NOW - timedelta(hours=20), 1), advice)
    assert (decision.mode, decision.minutes) == ("recovery", 10)
    assert "fewer than 3 sessions" in decision.reason


def test_decide_lock__rest_without_a_recent_session__recovery():
    assert _decide(_state(NOW - timedelta(days=2), None, 3), REST).mode == "recovery"


def test_decide_lock__already_two_rest_days__recovery():
    decision = _decide(_state(NOW - timedelta(hours=23), None, 3, rest_in_a_row=2), REST)
    assert decision.mode == "recovery"
    assert "2 rest days in a row" in decision.reason


def test_decide_lock__rest_days_off_in_settings__recovery():
    state = _state(NOW - timedelta(hours=23), None, 3)
    assert _decide(state, REST, Policy(allow_rest_days=False)).mode == "recovery"


def test_decide_lock__recovery_longer_than_the_lock__capped_at_the_lock():
    state = _state(NOW - timedelta(hours=24), NOW - timedelta(hours=24), 1)
    advice = CoachAdvice(mode="recovery", recovery_minutes=40)
    assert _decide(state, advice, minutes=20).minutes == 20


def test_rest_days_in_a_row__counts_back_from_yesterday():
    today = date(2026, 10, 6)
    assert rest_days_in_a_row({date(2026, 10, 5), date(2026, 10, 4), date(2026, 10, 2)}, today) == 2


def test_load_summary__one_session__minutes_times_effort():
    session = SessionSummary(
        id=1,
        day=date(2026, 10, 5),
        finished_at=NOW - timedelta(hours=24),
        day_type="hard",
        title="x",
        minutes=45,
        rpe=8,
    )
    summary = load_summary([session], 0, NOW)
    assert (summary["sessions_last_7_days"], summary["minutes_last_7_days"], summary["load_last_7_days"]) == (
        1,
        45,
        360,
    )
    assert len(summary["last_48h"]) == 1


def test_pace_and_transition__without_history__defaults():
    assert (pace_factor([]), transition_seconds([])) == (DEFAULT_PACE, DEFAULT_TRANSITION)


def test_pace_factor__exercises_took_twice_the_estimate__two():
    target = Target(sets=1, rest=0, seconds=60)
    timings = [ExerciseTiming(target=target, sets_done=1, started_at=NOW, ended_at=NOW + timedelta(seconds=120))] * 3
    assert pace_factor(timings) == 2.0


def test_transition_seconds__average_of_plausible_gaps():
    assert transition_seconds([30, 60, 90, 5000]) == 60

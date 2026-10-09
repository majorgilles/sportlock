from datetime import datetime, timedelta

from sportlock.progression.domain.ladders import (
    START,
    STARTING_POINTS,
    ExerciseResult,
    LadderPosition,
    moves_for_session,
    positions,
)
from sportlock.progression.domain.planner import is_recovery_day, local_plan
from sportlock.progression.domain.rules import SetResult
from sportlock.shared_kernel.targets import Target

HOUSE = {"chair", "table", "bench", "doorway"}
NOW = datetime(2026, 10, 5, 18, 0)


def _start_positions():
    return {p.chain: p for p in positions({})}


def test_start__every_chain__starts_on_an_exercise_of_that_chain(catalogue):
    for chain, (exercise, _) in START.items():
        assert catalogue.get(exercise).chain == chain


def test_starting_points__every_level__cover_every_chain_with_their_own_exercises(catalogue):
    for level, points in STARTING_POINTS.items():
        assert set(points) == set(START), level
        for chain, (exercise, _) in points.items():
            assert catalogue.get(exercise).chain == chain


def test_positions__nothing_saved__every_chain_at_its_start():
    assert {p.chain: p.exercise for p in positions({})} == {chain: e for chain, (e, _) in START.items()}


def test_local_plan__hard_day__warm_up_main_patterns_cool_down(catalogue):
    # when
    plan = local_plan(_start_positions(), catalogue, HOUSE, recovery=False, hours_since_hard=None)
    # then
    assert plan.day_type == "hard"
    assert [i.exercise for i in plan.items] == ["dynamic-warmup", "incline-push-up", "bodyweight-squat",
                                                "prone-y-raise", "glute-bridge", "plank", "static-stretch"]


def test_local_plan__position_with_a_reason__shown_on_the_card(catalogue):
    # given
    saved = _start_positions()
    saved["push-horizontal"] = LadderPosition(chain="push-horizontal", exercise="knee-push-up",
                                              target=Target(sets=3, rest=60, reps=(8, 12)), reason="top of the range")
    # when
    plan = local_plan(saved, catalogue, HOUSE, recovery=False, hours_since_hard=None)
    # then
    [push] = [i for i in plan.items if i.exercise == "knee-push-up"]
    assert push.target.progress == "top of the range"


def test_local_plan__missing_equipment__falls_back_down_the_chain(catalogue):
    plan = local_plan(_start_positions(), catalogue, set(), recovery=False, hours_since_hard=None)
    exercises = [i.exercise for i in plan.items]
    assert "wall-push-up" in exercises and "incline-push-up" not in exercises


def test_local_plan__recovery__mobility_day_with_available_stretches(catalogue):
    plan = local_plan(_start_positions(), catalogue, {"doorway"}, recovery=True, hours_since_hard=20)
    assert plan.day_type == "mobility"
    assert "chest-doorway-stretch" in [i.exercise for i in plan.items]
    assert plan.note == "Hard session 20 h ago"


def test_is_recovery_day__by_the_48_hour_rule():
    assert is_recovery_day(None, NOW - timedelta(hours=20), NOW)
    assert not is_recovery_day(None, NOW - timedelta(hours=49), NOW)
    assert not is_recovery_day("hard", NOW - timedelta(hours=20), NOW)
    assert is_recovery_day("recovery", None, NOW)


def test_moves_for_session__done_and_swapped_exercises__move_their_chains(catalogue):
    # given
    target = Target(sets=3, rest=60, reps=(8, 12))
    results = [
        ExerciseResult(session_exercise_id=1, exercise="dynamic-warmup", name="Dynamic warm-up", kind="timed",
                       target=Target(sets=1, seconds=300), status="done", rpe=3, sets=(SetResult(reps=None, seconds=300),)),
        ExerciseResult(session_exercise_id=2, exercise="incline-push-up", name="Incline push-up", kind="reps",
                       target=target, status="done", rpe=5, sets=(SetResult(reps=12, seconds=30),) * 3),
        ExerciseResult(session_exercise_id=3, exercise="bodyweight-squat", name="Bodyweight squat", kind="reps",
                       target=target, status="swapped", rpe=None, sets=()),
    ]
    # when
    moves = moves_for_session(results, catalogue)
    # then
    assert [(m.chain, m.proposal.rule, m.proposal.exercise) for m in moves] == [
        ("push-horizontal", "up", "knee-push-up"), ("squat", "too-hard", "wall-sit")]
    assert moves[1].proposal.target.seconds == 30 and moves[1].proposal.target.reps is None  # a hold now

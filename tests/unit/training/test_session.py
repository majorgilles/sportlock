from datetime import datetime, timedelta

import pytest

from sportlock.progression.domain.ladders import START, positions
from sportlock.progression.domain.planner import local_plan
from sportlock.shared_kernel.plans import PlannedExercise, SessionPlan
from sportlock.shared_kernel.targets import Target
from sportlock.training.domain.fitting import estimate_seconds, fit_plan
from sportlock.training.domain.session import TrainingError, TrainingSession

T0 = datetime(2026, 10, 5, 18, 0)
HOUSE = {"chair", "table", "bench", "doorway"}


def _t(seconds):
    return T0 + timedelta(seconds=seconds)


def _hard_plan(catalogue):
    return local_plan({p.chain: p for p in positions({})}, catalogue, HOUSE, recovery=False, hours_since_hard=None)


def _session(catalogue, plan=None):
    return TrainingSession.begin(plan or _hard_plan(catalogue), catalogue, now=T0, kind="scheduled", lock_key="k",
                                 source="local")


def _do_timed(session, start, length):
    session.start_set(_t(start))
    session.stop_set(_t(start + length))
    session.save_set(_t(start + length + 1))
    session.rate(_t(start + length + 5), 3)


def test_save_set__timed_exercise__records_the_measured_duration(catalogue):
    session = _session(catalogue)
    _do_timed(session, 0, 290)
    assert (session.exercises[0].sets[0].seconds, session.exercises[0].sets[0].reps) == (290, None)
    assert session.current == 1


def test_save_set__reps_sets__rest_measured_between_sets_then_rating(catalogue):
    # given
    session = _session(catalogue)
    _do_timed(session, 0, 300)
    # when
    session.start_set(_t(310))
    session.stop_set(_t(340))
    with pytest.raises(TrainingError):
        session.save_set(_t(341))  # reps required
    session.save_set(_t(341), reps=10)
    assert session.phase == "resting"
    session.start_set(_t(400)); session.stop_set(_t(425)); session.save_set(_t(426), reps=9)
    session.start_set(_t(490)); session.stop_set(_t(512)); session.save_set(_t(513), reps=8)
    assert session.phase == "rating"
    session.rate(_t(520), 7, "last set hard")
    # then
    push = session.exercises[1]
    assert [(s.reps, s.seconds, s.rest_seconds) for s in push.sets] == [(10, 30, None), (9, 25, 60), (8, 22, 65)]
    assert (push.status, push.rpe, push.note) == ("done", 7, "last set hard")


def test_swap_easier__current_exercise__replaced_by_the_easier_variation(catalogue):
    session = _session(catalogue)
    _do_timed(session, 0, 300)
    session.swap_easier(_t(305), catalogue, set(START))
    assert session.exercises[1].status == "swapped"
    assert session.exercises[2].name == "Wall push-up"
    assert session.exercises[1].swapped_to is session.exercises[2]
    assert session.current == 2


def test_swap_easier__warm_up__refused(catalogue):
    with pytest.raises(TrainingError):
        _session(catalogue).swap_easier(_t(1), catalogue, set(START))


def test_start_set__with_lead_in__set_timed_from_the_end_of_the_countdown(catalogue):
    session = _session(catalogue)
    session.start_set(_t(0), lead_in=5)
    with pytest.raises(TrainingError):
        session.stop_set(_t(3))  # still in the countdown
    session.stop_set(_t(65))
    session.save_set(_t(66))
    assert session.exercises[0].sets[0].seconds == 60


def test_go_now_and_cancel_set__during_the_lead_in(catalogue):
    session = _session(catalogue)
    session.start_set(_t(0), lead_in=5)
    session.go_now(_t(2))
    assert session.set_started_at == _t(2)
    session.stop_set(_t(30)); session.save_set(_t(31)); session.rate(_t(32), 3)
    session.start_set(_t(40), lead_in=5)
    session.cancel_set(_t(42))
    assert session.phase == "ready"
    session.start_set(_t(50), lead_in=5)
    with pytest.raises(TrainingError):
        session.cancel_set(_t(60))  # already running


def test_skip__needs_a_reason(catalogue):
    session = _session(catalogue)
    with pytest.raises(TrainingError):
        session.skip(_t(1), "")
    session.skip(_t(1), "shoulder pain")
    assert (session.exercises[0].status, session.exercises[0].skip_reason) == ("skipped", "shoulder pain")


def test_finish__after_the_last_exercise__finished_with_the_summary(catalogue):
    session = _session(catalogue)
    for _ in session.exercises:
        session.skip(_t(1), "testing")
    assert session.phase == "summary"
    session.finish(_t(60), 6, "ok", 200, 120, 78.5)
    assert (session.status, session.rpe, session.calories, session.avg_hr, session.body_weight) == (
        "finished", 6, 200, 120, 78.5)


def test_close__time_ran_out_in_the_cool_down__counts_as_finished(catalogue):
    session = _session(catalogue)
    for _ in session.exercises[:-1]:
        session.start_set(_t(0)); session.stop_set(_t(30))
        session.save_set(_t(31), reps=10)
        session.end_sets(_t(32)) if session.phase != "rating" else None
        session.rate(_t(33), 6)
    session.close(_t(1200), "abandoned")
    assert session.status == "finished"
    assert "main work" in session.notes


def test_close__main_work_skipped__abandoned(catalogue):
    session = _session(catalogue)
    session.skip(_t(1), "late")
    session.close(_t(1200), "abandoned")
    assert session.status == "abandoned"


def test_begin__exercises_done_on_both_sides__targets_carry_sides(catalogue):
    plan = SessionPlan(title="Sides", day_type="hard", items=(
        PlannedExercise(exercise="dead-bug", target=Target(sets=1, rest=30, reps=(8, 10))),
        PlannedExercise(exercise="hip-flexor-stretch", target=Target(sets=1, rest=15, seconds=30)),
        PlannedExercise(exercise="glute-bridge", target=Target(sets=1, rest=30, reps=(12, 15)))))
    session = _session(catalogue, plan)
    assert [e.target.sides for e in session.exercises] == ["alternating", "each", None]


def test_save_set__hold_on_each_side__logged_per_side(catalogue):
    plan = SessionPlan(title="Sides", day_type="hard", items=(
        PlannedExercise(exercise="hip-flexor-stretch", target=Target(sets=1, rest=15, seconds=30)),
        PlannedExercise(exercise="glute-bridge", target=Target(sets=1, rest=30, reps=(12, 15)))))
    session = _session(catalogue, plan)
    session.start_set(_t(0)); session.stop_set(_t(62)); session.save_set(_t(63))
    assert session.exercises[0].sets[0].seconds == 31


def test_fit_plan__long_lock__unchanged(catalogue):
    plan = _hard_plan(catalogue)
    assert fit_plan(plan, 120) == plan


def test_fit_plan__short_lock__shrinks_but_keeps_warm_up_and_cool_down(catalogue):
    fitted = fit_plan(_hard_plan(catalogue), 15)
    assert estimate_seconds(fitted.items) <= 15 * 60
    assert (fitted.items[0].exercise, fitted.items[-1].exercise) == ("dynamic-warmup", "static-stretch")
    assert len(fitted.items) >= 3


def test_fit_plan__with_transitions__counted_in_the_estimate(catalogue):
    plan = _hard_plan(catalogue)
    fitted = fit_plan(plan, 20, pace=1.0, transition=40)
    assert estimate_seconds(fitted.items, pace=1.0, transition=40) <= 20 * 60


def test_estimate_seconds__both_sides__count_twice():
    one = PlannedExercise(exercise="hip-flexor-stretch", target=Target(sets=2, rest=0, seconds=30))
    both = PlannedExercise(exercise="hip-flexor-stretch", target=Target(sets=2, rest=0, seconds=30, sides="each"))
    assert estimate_seconds([both]) == 2 * estimate_seconds([one])


def test_for_kind__between_reps_and_holds__converts_the_target():
    assert Target(sets=2, rest=15, reps=(8, 10)).for_kind("timed") == Target(sets=2, rest=15, seconds=120)
    assert Target(sets=3, rest=45, seconds=30).for_kind("reps") == Target(sets=3, rest=45, reps=(8, 12))
    assert Target(sets=3, rest=45, seconds=30).for_kind("hold") == Target(sets=3, rest=45, seconds=30)

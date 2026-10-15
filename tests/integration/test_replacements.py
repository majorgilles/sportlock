import copy
from datetime import timedelta

from sportlock.progression.domain.ladders import LadderMove, LadderPosition, positions
from sportlock.progression.domain.rules import Proposal
from sportlock.shared_kernel.targets import Target
from tests.world import GOOD_PLAN, item

SQUATS = Target(sets=3, rest=60, reps=(12, 15))
STEP_UPS = Target(sets=3, rest=60, reps=(8, 12))


def _squats_became_step_ups(world):
    """The last session, two days ago, moved the squat ladder up to step-ups."""
    world.add_session(world.clock.time - timedelta(hours=50), rpe=4)
    session_id = world.container.training_history.last_counted_id()
    proposal = Proposal(rule="up", exercise="step-up", target=STEP_UPS, reason="15 / 15 reps: top of the range")
    world.container.ladder_repository.record_move(
        session_id,
        LadderMove(
            chain="squat",
            session_exercise_id=0,
            from_exercise="bodyweight-squat",
            from_target=SQUATS,
            proposal=proposal,
        ),
        decided_by="rules",
        now=world.clock.time,
    )
    world.container.ladder_repository.set(
        LadderPosition(chain="squat", exercise="step-up", target=STEP_UPS), world.clock.time
    )


def _plan_with_step_ups(world):
    output = copy.deepcopy(GOOD_PLAN)
    output["hard"]["exercises"][2] = item("step-up", 3, (8, 12), note="Your first step-ups")
    world.coach.output = output
    world.container.run_coach_service.execute(world.daemon.coach_command(world.clock.time))


def _squat(world):
    return {p.chain: p for p in positions(world.container.ladder_repository.saved())}["squat"]


def test_warning_popup__exercise_replaced_after_the_last_session__asks_about_it(world):
    _squats_became_step_ups(world)
    world.at("17:50")
    [replacement] = world.popup.shown[0]["replacements"]
    assert replacement == {
        "session": world.container.training_history.last_counted_id(),
        "chain": "squat",
        "from": "bodyweight-squat",
        "from_name": "Bodyweight squat",
        "to": "step-up",
        "to_name": "Step-up",
        "reason": "15 / 15 reps: top of the range",
        "by": "rules",
    }


def test_replacement_answer__agree__nothing_changes_and_no_longer_asked(world):
    _squats_became_step_ups(world)
    assert world.cmd(cmd="replacement-answer", chain="squat", agree=True)["ok"]
    assert world.container.pending_replacements_service.execute() == []
    assert _squat(world).exercise == "step-up"


def test_replacement_answer__keep__ladder_and_coach_plan_go_back_to_the_old_exercise(world):
    # given
    _squats_became_step_ups(world)
    _plan_with_step_ups(world)
    # when
    assert world.cmd(cmd="replacement-answer", chain="squat", agree=False, note="My knee likes squats")["ok"]
    # then
    squat = _squat(world)
    assert (squat.exercise, squat.target.reps) == ("bodyweight-squat", (12, 15))
    assert squat.reason == "You kept Bodyweight squat instead of Step-up: My knee likes squats"
    plan = world.container.coach_plan_freshness.fresh_plan()
    assert [i.exercise for i in plan.hard.items][2] == "bodyweight-squat"
    assert world.container.pending_replacements_service.execute() == []


def test_replacement_answer__keep_during_the_lock__session_serves_the_old_exercise(world):
    # given: the lock started with step-ups in the session
    _squats_became_step_ups(world)
    _plan_with_step_ups(world)
    state = world.at("18:00")
    assert "Step-up" in [e["name"] for e in state["training"]["exercises"]]
    assert [r["chain"] for r in state["replacements"]] == ["squat"]
    # when
    assert world.cmd(cmd="replacement-answer", chain="squat", agree=False)["ok"]
    # then
    state = world.at("18:00", second=1)
    names = [e["name"] for e in state["training"]["exercises"]]
    assert "Bodyweight squat" in names and "Step-up" not in names
    assert state["replacements"] == []


def test_replacement_answer__next_coach_run__sees_the_answer(world):
    _squats_became_step_ups(world)
    world.cmd(cmd="replacement-answer", chain="squat", agree=False, note="knee")
    _plan_with_step_ups(world)
    assert world.coach.contexts[-1]["answers_to_replacements"] == [
        {
            "on": "2026-10-05",
            "chain": "squat",
            "replaced": "Bodyweight squat",
            "by": "Step-up",
            "answer": "kept the replaced exercise",
            "note": "knee",
        }
    ]


def test_replacement_answer__nothing_replaced_on_that_chain__error(world):
    _squats_became_step_ups(world)
    answer = world.cmd(cmd="replacement-answer", chain="core", agree=True)
    assert answer == {"ok": False, "error": "no replaced exercise to answer on that chain"}

import copy
import json
from datetime import timedelta

import pytest

from sportlock.coaching.domain.plan import CoachOutputError
from sportlock.coaching.domain.ports import CoachUnavailableError
from sportlock.progression.domain.ladders import START, positions
from tests.world import GOOD_PLAN, item


def _run(world, output=None):
    world.coach.output = copy.deepcopy(output if output is not None else GOOD_PLAN)
    return world.c.run_coach.execute(world.daemon.coach_command(world.clock.time))


def test_run_coach__good_answer__plan_stored_fresh_and_announced(world):
    assert world.c.freshness.needs_run()
    plan = _run(world)
    assert not world.c.freshness.needs_run()
    assert world.c.freshness.fresh_plan().rationale == GOOD_PLAN["rationale"]
    assert plan.hard.items[3].target.seconds == 40
    assert world.c.coach_runs.recent(1)[0].ok
    assert "Next session planned" in world.desktop.notifications


def test_run_coach__bad_answer__rejected_and_logged(world):
    output = copy.deepcopy(GOOD_PLAN)
    output["hard"]["exercises"][1] = item("burpee-deluxe", 3, 8, 12)
    with pytest.raises(CoachOutputError):
        _run(world, output)
    assert world.c.freshness.fresh_plan() is None
    assert not world.c.coach_runs.recent(1)[0].ok


def test_run_coach__coach_unavailable__logged(world):
    world.coach.output = None
    with pytest.raises(CoachUnavailableError):
        world.c.run_coach.execute(world.daemon.coach_command(world.clock.time))
    assert world.c.coach_runs.recent(1)[0].error == "no answer prepared"


def test_run_coach__recommendations__answer_the_last_session_and_are_notified(world):
    world.add_session(world.clock.time - timedelta(hours=20))
    session_id = world.c.history.last_counted_id()
    plan = _run(world, {**GOOD_PLAN, "recommendations": [{"about": "Push-ups too easy", "advice": "Use a desk."}]})
    assert plan.feedback_session == session_id
    assert "Coach's feedback on your session" in world.desktop.notifications
    assert _run(world).feedback_session is None  # nothing to answer


def test_fresh_plan__after_a_new_session__stale(world):
    _run(world)
    world.add_session(world.clock.time)
    assert world.c.freshness.fresh_plan() is None and world.c.freshness.needs_run()


def test_run_coach__override__moves_the_ladder_and_marks_the_rule_move(world):
    # given: the rules moved the squat up after the last session
    world.c.database.execute(
        "INSERT INTO proposals (session_id, chain, rule, from_exercise, from_target, to_exercise, to_target, reason,"
        " status, decided_by, created_at) VALUES (1, 'squat', 'up', 'bodyweight-squat', '{}', 'split-squat', '{}',"
        " 'top of range', 'applied', 'rules', '2026-10-05T17:00:00')")
    output = copy.deepcopy(GOOD_PLAN)
    output["ladder_overrides"] = [{"chain": "squat", **item("bodyweight-squat", 3, 12, 15), "reason": "Knee pain in notes"}]
    # when
    _run(world, output)
    # then
    squat = {p.chain: p for p in positions(world.c.ladders.saved())}["squat"]
    assert (squat.exercise, squat.reason) == ("bodyweight-squat", "Coach: Knee pain in notes")
    rows = world.c.database.execute("SELECT status, decided_by, override_reason FROM proposals ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [("overridden", "rules", "Knee pain in notes"), ("applied", "agent", None)]


def test_run_coach__memory_operations__versioned_notes_fed_back_next_time(world):
    # given
    _run(world, {**GOOD_PLAN, "memory": [{"op": "add", "id": None, "topic": "body", "note": "Right shoulder pinches."},
                                         {"op": "add", "id": None, "topic": "plans", "note": "Wants 30 minutes."}]})
    # when: three days later the coach refines one note and drops the other
    world.clock.time += timedelta(days=3)
    _run(world, {**GOOD_PLAN, "memory": [{"op": "update", "id": 2, "topic": None, "note": "Moved to 30 minutes."},
                                         {"op": "delete", "id": 1, "topic": None, "note": None},
                                         {"op": "update", "id": 99, "topic": None, "note": "ghost"}]})
    # then
    notes = world.cmd(cmd="memory-get")["notes"]
    assert [(n["id"], n["note"], n["since"]) for n in notes] == [(2, "Moved to 30 minutes.", "2026-10-08")]
    history = world.cmd(cmd="memory-history")["history"]
    assert len(history) == 3  # two first versions (now ended) and the update
    assert {h["end_reason"] for h in history} == {None, "updated", "deleted"}
    assert world.coach.contexts[-1]["coach_memory"][0]["note"] == "Right shoulder pinches."  # what it saw last run


def test_forget__a_note__removed_and_reported_to_the_coach(world):
    _run(world, {**GOOD_PLAN, "memory": [{"op": "add", "id": None, "topic": "body", "note": "Left knee twinges."}]})
    assert world.cmd(cmd="memory-forget", id=1)["ok"]
    assert not world.cmd(cmd="memory-forget", id=1)["ok"]
    assert world.cmd(cmd="memory-get")["notes"] == []
    _run(world)
    assert world.coach.contexts[-1]["forgotten_by_user"] == ["Left knee twinges."]


def test_context__what_the_coach_sees__profile_ladders_catalogue_serialisable(world):
    _run(world)
    context = world.coach.contexts[-1]
    assert context["profile"]["experience"] == "beginner"
    assert len(context["ladders"]) == len(START)
    assert any(c["id"] == "pull-up" and not c["available"] for c in context["catalogue"])
    assert context["upcoming_locks"][0]["minutes"] == 30
    json.dumps(context)


def test_begin__fresh_coach_plan__served_with_its_rationale(world):
    _run(world)
    training = world.at("18:00")["training"]
    assert (training["title"], training["source"], training["note"]) == ("Push focus", "generated", GOOD_PLAN["rationale"])
    assert training["exercises"][1]["name"] == "Knee push-up"
    assert training["exercises"][1]["target"]["progress"] == "Up a step: 3×12 felt easy"


def test_agent_status__plan_and_runs(world):
    _run(world)
    status = world.cmd(cmd="agent-status")
    assert status["plan"]["hard"]["title"] == "Push focus" and status["runs"][-1]["ok"]

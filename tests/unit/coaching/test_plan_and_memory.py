import copy
from datetime import date, datetime, timedelta

import pytest

from sportlock.coaching.domain.memory import (
    MAX_NOTES,
    CoachMemory,
    CoachMemoryError,
    MemoryOperation,
    NoteAdded,
    NoteDeleted,
    NoteForgotten,
    NoteUpdated,
)
from sportlock.coaching.domain.plan import CoachOutputError, CoachPlan, parse_coach_output
from sportlock.progression.domain.ladders import START
from tests.world import GOOD_PLAN, item

HOUSE = {"chair", "table", "bench", "doorway"}
TODAY = date(2026, 10, 5)
NOW = datetime(2026, 10, 5, 18, 0)


def _parse(output, catalogue):
    return parse_coach_output(output, catalogue, HOUSE, set(START))


def test_parse_coach_output__good_answer__plan_with_targets(catalogue):
    parsed = _parse(GOOD_PLAN, catalogue)
    push = parsed.plan.hard.items[1]
    assert (push.exercise, push.target.reps, push.target.progress) == (
        "knee-push-up",
        (8, 12),
        "Up a step: 3×12 felt easy",
    )
    assert parsed.plan.hard.items[3].target.seconds == 40
    assert parsed.plan.recovery.day_type == "mobility"


@pytest.mark.parametrize(
    "label, mutate",
    [
        ("unknown exercise", lambda o: o["hard"]["exercises"].__setitem__(1, item("burpee-deluxe", 3, 8, 12))),
        ("missing equipment", lambda o: o["hard"]["exercises"].__setitem__(1, item("pull-up", 3, 5, 8))),
        ("reps on a hold", lambda o: o["hard"]["exercises"].__setitem__(3, item("plank", 3, 8, 12))),
        ("too many sets", lambda o: o["hard"]["exercises"].__setitem__(2, item("bodyweight-squat", 9, 12, 15))),
        (
            "override without reason",
            lambda o: o["ladder_overrides"].append({"chain": "squat", **item("box-squat", 3, 10, 12), "reason": " "}),
        ),
        (
            "override on the wrong chain",
            lambda o: o["ladder_overrides"].append(
                {"chain": "core", **item("box-squat", 3, 10, 12), "reason": "knee pain"}
            ),
        ),
    ],
)
def test_parse_coach_output__bad_answer__rejected(catalogue, label, mutate):
    output = copy.deepcopy(GOOD_PLAN)
    mutate(output)
    with pytest.raises(CoachOutputError):
        _parse(output, catalogue)


def test_parse_coach_output__recommendations__trimmed_and_empty_ones_dropped(catalogue):
    tips = [{"about": " Push-ups too easy ", "advice": " Go down to the bench. "}, {"about": "empty", "advice": " "}]
    parsed = _parse({**GOOD_PLAN, "recommendations": tips}, catalogue)
    assert [r.model_dump() for r in parsed.plan.recommendations] == [
        {"about": "Push-ups too easy", "advice": "Go down to the bench."}
    ]


def test_version_for__by_the_lock_decision_or_the_48_hour_rule(catalogue):
    plan = _parse(GOOD_PLAN, catalogue).plan
    assert plan.version_for(None, last_hard=None, now=NOW).day_type == "hard"
    assert plan.version_for(None, last_hard=NOW - timedelta(hours=20), now=NOW).day_type == "mobility"
    assert plan.version_for(None, last_hard=NOW - timedelta(hours=50), now=NOW).day_type == "hard"
    assert plan.version_for("hard", last_hard=NOW - timedelta(hours=20), now=NOW).day_type == "hard"


def test_coach_plan__stored_shape__round_trips(catalogue):
    plan = _parse(GOOD_PLAN, catalogue).plan.model_copy(update={"basis": "session:3/profile:x"})
    assert CoachPlan.from_dict(plan.to_dict()) == plan


def _add(topic, text):
    return MemoryOperation(op="add", topic=topic, text=text)


def test_apply__add_update_delete__current_notes_and_events():
    # given
    memory = CoachMemory()
    memory.apply(
        [_add("body", "Right shoulder pinches overhead."), _add("plans", "Wants 30-minute sessions.")],
        by="coach-run:1",
        today=TODAY,
    )
    memory.pull_events()
    # when
    refused = memory.apply(
        [
            MemoryOperation(op="update", id=2, text="  Moved to 30-minute\n sessions. "),
            MemoryOperation(op="delete", id=1),
        ],
        by="coach-run:2",
        today=TODAY + timedelta(days=2),
    )
    # then
    assert refused == []
    assert [(n.id, n.text, n.since) for n in memory.current()] == [
        (2, "Moved to 30-minute sessions.", TODAY + timedelta(days=2))
    ]
    assert [type(e) for e in memory.pull_events()] == [NoteUpdated, NoteDeleted]


def test_apply__unknown_ids_and_empty_adds__refused_without_losing_the_rest():
    memory = CoachMemory()
    refused = memory.apply(
        [MemoryOperation(op="update", id=9, text="x"), _add("body", " "), _add("body", "Knee ok.")],
        by="coach-run:1",
        today=TODAY,
    )
    assert len(refused) == 2
    assert [n.text for n in memory.current()] == ["Knee ok."]
    assert [type(e) for e in memory.pull_events()] == [NoteAdded]


def test_apply__beyond_the_limit__extra_adds_refused():
    memory = CoachMemory()
    refused = memory.apply([_add("progress", f"note {i}") for i in range(MAX_NOTES + 2)], by="coach-run:1", today=TODAY)
    assert (len(memory.current()), len(refused)) == (MAX_NOTES, 2)


def test_apply__update_without_change__no_event():
    memory = CoachMemory()
    memory.apply([_add("body", "Knee ok.")], by="coach-run:1", today=TODAY)
    memory.pull_events()
    memory.apply([MemoryOperation(op="update", id=1, text="Knee ok.")], by="coach-run:2", today=TODAY)
    assert memory.pull_events() == []


def test_forget__a_note__removed_with_a_forgotten_event():
    memory = CoachMemory()
    memory.apply([_add("body", "Left knee twinges.")], by="coach-run:1", today=TODAY)
    memory.pull_events()
    memory.forget(1, today=TODAY)
    assert memory.current() == []
    [event] = memory.pull_events()
    assert isinstance(event, NoteForgotten) and event.text == "Left knee twinges."
    with pytest.raises(CoachMemoryError):
        memory.forget(1, today=TODAY)

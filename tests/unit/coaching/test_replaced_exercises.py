import pytest

from sportlock.coaching.domain.plan import CoachOutputError, CoachPlan
from sportlock.coaching.domain.replacements import ReplacementAnswer, replacements_after
from sportlock.coaching.domain.review import parse_review
from sportlock.shared_kernel.targets import Target

SQUAT_TARGET = {"sets": 3, "rest": 60, "reps": [12, 15]}


def _move(chain, frm, to, reason, decided_by="rules", from_target=SQUAT_TARGET):
    return {
        "chain": chain,
        "from": frm,
        "from_target": from_target,
        "to": to,
        "reason": reason,
        "decided_by": decided_by,
    }


def test_replacements_after__rule_moved_a_chain_up__replacement_with_the_old_target(catalogue):
    moves = [_move("squat", "bodyweight-squat", "step-up", "15 / 15 reps: top of the range")]
    [replacement] = replacements_after(14, moves, {"squat": "step-up"}, catalogue)
    assert (replacement.from_name, replacement.to_name, replacement.reason) == (
        "Bodyweight squat",
        "Step-up",
        "15 / 15 reps: top of the range",
    )
    assert replacement.from_target == Target(sets=3, rest=60, reps=(12, 15))


def test_replacements_after__coach_override_kept_the_new_exercise__reason_of_the_move_that_brought_it(catalogue):
    moves = [
        _move("squat", "bodyweight-squat", "step-up", "top of the range"),
        _move("squat", "step-up", "step-up", "fewer reps for the knee", "agent", {"sets": 2, "reps": [8, 12]}),
    ]
    [replacement] = replacements_after(14, moves, {"squat": "step-up"}, catalogue)
    assert (replacement.from_exercise, replacement.reason, replacement.to_dict()["by"]) == (
        "bodyweight-squat",
        "top of the range",
        "rules",
    )


def test_replacements_after__same_exercise_or_moved_back__nothing(catalogue):
    moves = [
        _move("core", "plank", "plank", "same again"),
        _move("squat", "bodyweight-squat", "step-up", "top of the range"),
    ]
    assert replacements_after(14, moves, {"core": "plank", "squat": "bodyweight-squat"}, catalogue) == []


def test_replacements_after__unreadable_old_target__skipped(catalogue):
    moves = [_move("squat", "bodyweight-squat", "step-up", "top", from_target={})]
    assert replacements_after(14, moves, {"squat": "step-up"}, catalogue) == []


def test_replacement_answer__long_note__trimmed_to_one_paragraph(catalogue):
    [replacement] = replacements_after(
        14, [_move("squat", "bodyweight-squat", "step-up", "top")], {"squat": "step-up"}, catalogue
    )
    answer = ReplacementAnswer.to(replacement, agreed=False, note="my\n knee " + "x" * 400, answered_at="t")
    assert answer.note.startswith("my knee x") and len(answer.note) == 300


def test_coach_plan_keeping__replaced_exercise_in_the_plan__old_one_back_in_both_versions():
    plan = CoachPlan.from_dict(
        {
            "rationale": "",
            "hard": {"title": "H", "day_type": "hard", "plan": [{"exercise": "step-up", "sets": 2, "reps": [8, 12]}]},
            "recovery": {"title": "R", "day_type": "light", "plan": [{"exercise": "cat-cow", "sets": 1}]},
        }
    )
    kept = plan.keeping("step-up", "bodyweight-squat", Target(sets=3, rest=60, reps=(12, 15)), "You kept it.")
    assert kept.hard.items[0].to_dict() == {
        "exercise": "bodyweight-squat",
        "sets": 3,
        "rest": 60,
        "reps": [12, 15],
        "progress": "You kept it.",
    }
    assert kept.recovery == plan.recovery


def test_parse_review__without_a_dossier__rejected():
    with pytest.raises(CoachOutputError):
        parse_review({"dossier": " ", "last_session": "ok", "improve": []}, session_id=1, written_at="t")


def test_parse_review__advice_without_text_and_too_many__dropped_and_capped():
    raw = {"dossier": "d", "last_session": "l", "improve": [{"area": "a", "advice": ""}]}
    raw["improve"] += [{"area": f"a{i}", "advice": "x"} for i in range(6)]
    review = parse_review(raw, session_id=1, written_at="t")
    assert [i.area for i in review.improve] == ["a0", "a1", "a2", "a3"]

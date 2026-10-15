import copy
from datetime import timedelta

from tests.world import GOOD_PLAN

REVIEW = {
    "dossier": "A careful beginner with a sore knee.",
    "last_session": "Easy session; your crunches are ready to move on.",
    "improve": [{"area": "Effort", "advice": "Finish sets closer to your limit."}],
}


def _trained(world):
    world.add_session(world.clock.time - timedelta(hours=20), minutes=30, rpe=4)


def test_trainee_report__after_a_session__stats_and_journal_without_the_coach(world):
    _trained(world)
    report = world.cmd(cmd="trainee-report")
    assert report["ok"] and not report["writing"]
    body = report["report"]
    assert (body["stats"]["sessions"], body["stats"]["minutes"]) == (1, 30)
    assert body["dossier"] is None and body["last_session"]["minutes"] == 30
    assert [e["id"] for e in body["journal"]] == [body["last_session"]["id"]]


def test_write_review__coach_answers__dossier_advice_and_the_session_review_shown(world):
    # given
    _trained(world)
    world.coach.review = REVIEW
    # when
    world.c.write_review.execute(world.daemon.coach_command(world.clock.time))
    # then
    body = world.cmd(cmd="trainee-report")["report"]
    assert body["dossier"]["text"] == "A careful beginner with a sore knee."
    assert body["improve"]["coach"] == [{"area": "Effort", "advice": "Finish sets closer to your limit."}]
    assert body["journal"][0]["coach"]["last_session"] == "Easy session; your crunches are ready to move on."
    assert world.coach.contexts[-1]["stats"]["sessions"] == 1


def test_run_coach__recommendations__kept_in_the_journal_with_a_later_review(world):
    # given: the plan answered the session's feedback
    _trained(world)
    world.coach.output = {
        **copy.deepcopy(GOOD_PLAN),
        "recommendations": [{"about": "Abs", "advice": "Lift the knees."}],
    }
    world.c.run_coach.execute(world.daemon.coach_command(world.clock.time))
    # when
    world.coach.review = REVIEW
    world.c.write_review.execute(world.daemon.coach_command(world.clock.time))
    # then
    coach = world.cmd(cmd="trainee-report")["report"]["journal"][0]["coach"]
    assert coach["recommendations"] == [{"about": "Abs", "advice": "Lift the knees."}]
    assert coach["last_session"] == REVIEW["last_session"]


def test_trainee_report_write__before_any_session__refused(world):
    answer = world.cmd(cmd="trainee-report-write")
    assert answer == {"ok": False, "error": "train once first: the coach reviews your sessions"}


def test_coach_run_in_the_background__plan_made__review_written_after_it(world):
    _trained(world)
    world.coach.output = copy.deepcopy(GOOD_PLAN)
    world.coach.review = REVIEW
    world.daemon._run_coach(world.daemon.coach_command(world.clock.time))
    assert world.cmd(cmd="trainee-report")["report"]["dossier"]["text"] == REVIEW["dossier"]

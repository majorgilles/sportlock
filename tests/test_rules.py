import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from sportlock.ladders import Ladders
from sportlock.library import Library
from sportlock.rules import propose
from sportlock.store import Store
from sportlock.training import Training

EASIER = ("wall-push-up", "Wall push-up")
HARDER = ("knee-push-up", "Knee push-up")
REPS = {"sets": 3, "reps": [8, 12], "rest": 60}
HOLD = {"sets": 3, "seconds": 30, "rest": 45}


def reps(*values):
    return [{"reps": v, "seconds": 30} for v in values]


def holds(*values):
    return [{"reps": None, "seconds": v} for v in values]


def run(target, sets, rpe, kind="reps", easier=EASIER, harder=HARDER):
    return propose(exercise="incline-push-up", name="Incline push-up", kind=kind, target=target, sets=sets,
                   rpe=rpe, easier=easier, harder=harder)


class RepsRulesTest(unittest.TestCase):
    def test_easy_top_of_range_moves_up(self):
        p = run(REPS, reps(12, 12, 13), 5)
        self.assertEqual((p.rule, p.exercise, p.target["reps"]), ("up", "knee-push-up", [8, 12]))

    def test_moderate_top_of_range_adds_a_rep(self):
        p = run(REPS, reps(12, 12, 12), 7)
        self.assertEqual((p.rule, p.exercise, p.target["reps"]), ("add", "incline-push-up", [9, 13]))

    def test_at_twenty_moderate_moves_up_and_resets_range(self):
        p = run({**REPS, "reps": [16, 20]}, reps(20, 20, 20), 8)
        self.assertEqual((p.rule, p.exercise, p.target["reps"]), ("up", "knee-push-up", [8, 12]))

    def test_very_hard_but_done_holds(self):
        self.assertEqual(run(REPS, reps(12, 12, 12), 9).rule, "hold")

    def test_inside_range_holds(self):
        p = run(REPS, reps(11, 10, 9), 7)
        self.assertEqual((p.rule, p.exercise), ("hold", "incline-push-up"))

    def test_below_range_moves_down(self):
        p = run(REPS, reps(10, 8, 6), 7)
        self.assertEqual((p.rule, p.exercise), ("down", "wall-push-up"))

    def test_fewer_sets_moves_down(self):
        self.assertEqual(run(REPS, reps(10, 9), 6).rule, "down")

    def test_grinding_moves_down(self):
        self.assertEqual(run(REPS, reps(10, 9, 9), 9).rule, "down")

    def test_easiest_variation_reduces_reps(self):
        p = run(REPS, reps(5, 5, 4), 9, easier=None)
        self.assertEqual((p.rule, p.exercise, p.target["reps"]), ("down", "incline-push-up", [6, 10]))

    def test_hardest_variation_adds_reps(self):
        p = run(REPS, reps(12, 12, 12), 5, harder=None)
        self.assertEqual((p.rule, p.target["reps"]), ("add", [10, 14]))

    def test_no_rating_no_proposal(self):
        self.assertIsNone(run(REPS, reps(12, 12, 12), None))


class HoldRulesTest(unittest.TestCase):
    def test_easy_adds_ten_seconds(self):
        p = run(HOLD, holds(31, 30, 32), 5, kind="hold")
        self.assertEqual((p.rule, p.target["seconds"]), ("add", 40))

    def test_moderate_adds_five(self):
        self.assertEqual(run(HOLD, holds(30, 30, 30), 7, kind="hold").target["seconds"], 35)

    def test_sixty_seconds_easy_moves_up_restarting_at_twenty(self):
        p = run({**HOLD, "seconds": 60}, holds(60, 61, 60), 6, kind="hold")
        self.assertEqual((p.rule, p.exercise, p.target["seconds"]), ("up", "knee-push-up", 20))

    def test_short_holds_move_down(self):
        p = run(HOLD, holds(30, 20, 18), 8, kind="hold")
        self.assertEqual((p.rule, p.exercise), ("down", "wall-push-up"))

    def test_timed_blocks_never_change(self):
        self.assertIsNone(run({"sets": 1, "seconds": 300, "rest": 0}, holds(300), 3, kind="timed"))


class LadderTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.store = Store(tmp / "db")
        self.library = Library(root=tmp / "library")
        self.training = Training(self.store, library=self.library)
        self.t0 = datetime(2026, 10, 5, 18, 0)

    def session(self, start, push_reps, push_rpe, kind="scheduled"):
        """Warm-up, then the push exercise with the given reps, skip the rest, finish."""
        tr, t = self.training, start
        tr.begin(now=t, kind=kind, lock_key=None, minutes=60, equipment={"chair", "table", "bench", "doorway"})
        tr.start_set(now=t); tr.stop_set(now=t + timedelta(seconds=300)); tr.save_set(now=t)
        tr.rate(now=t, rpe=3)
        for r in push_reps:
            tr.start_set(now=t); tr.stop_set(now=t + timedelta(seconds=30)); tr.save_set(now=t, reps=r)
        tr.rate(now=t, rpe=push_rpe)
        while tr.run["phase"] != "summary":
            tr.skip(now=t, reason="test")
        tr.finish(now=t + timedelta(minutes=30), rpe=6)

    def test_finishing_moves_the_ladder_and_next_plan_uses_it(self):
        self.session(self.t0, [12, 12, 12], 5)
        position = Ladders(self.store, self.library).get("push-horizontal")
        self.assertEqual(position["exercise"], "knee-push-up")
        self.assertIn("Knee push-up", position["reason"])

        later = self.t0 + timedelta(days=3)
        plan = Ladders(self.store, self.library).plan(later, {"chair", "table", "bench", "doorway"})
        push = [i for i in plan["plan"] if i["exercise"] == "knee-push-up"]
        self.assertEqual(len(push), 1)
        self.assertIn("top of the range", push[0]["progress"])

    def test_mobility_day_within_48h_of_hard_session(self):
        self.session(self.t0, [10, 10, 10], 7)
        plan = Ladders(self.store, self.library).plan(self.t0 + timedelta(hours=20), {"doorway"})
        self.assertEqual(plan["day_type"], "mobility")
        self.assertIn("chest-doorway-stretch", [i["exercise"] for i in plan["plan"]])
        hard_again = Ladders(self.store, self.library).plan(self.t0 + timedelta(hours=49), set())
        self.assertEqual(hard_again["day_type"], "hard")

    def test_test_sessions_do_not_move_ladders(self):
        self.session(self.t0, [12, 12, 12], 5, kind="test")
        self.assertEqual(Ladders(self.store, self.library).get("push-horizontal")["exercise"], "incline-push-up")

    def test_missing_equipment_falls_back_down_the_chain(self):
        plan = Ladders(self.store, self.library).plan(self.t0, set())  # no bench: incline push-up impossible
        names = [i["exercise"] for i in plan["plan"]]
        self.assertIn("wall-push-up", names)
        self.assertNotIn("incline-push-up", names)

    def test_too_hard_swap_moves_down(self):
        tr, t = self.training, self.t0
        tr.begin(now=t, kind="scheduled", lock_key=None, minutes=60, equipment={"bench"})
        tr.skip(now=t, reason="test")
        tr.swap_easier(now=t)
        while tr.run["phase"] != "summary":
            tr.skip(now=t, reason="test")
        tr.finish(now=t, rpe=5)
        position = Ladders(self.store, self.library).get("push-horizontal")
        self.assertEqual(position["exercise"], "wall-push-up")
        rule = self.store.db.execute("SELECT rule FROM proposals").fetchone()["rule"]
        self.assertEqual(rule, "too-hard")


if __name__ == "__main__":
    unittest.main()

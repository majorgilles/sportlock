import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from sportlock import profile as profile_mod
from sportlock.agent import PLAN_KEY, Agent, AgentError, choose
from sportlock.ladders import Ladders
from sportlock.library import Library, load_seed
from sportlock.store import Store
from sportlock.training import Training

NOW = datetime(2026, 10, 5, 18, 0)
HOUSE = {"chair", "table", "bench", "doorway"}


def item(exercise, sets=3, lo=None, hi=None, seconds=None, rest=60, note=""):
    return {"exercise": exercise, "sets": sets, "reps_low": lo, "reps_high": hi, "seconds": seconds, "rest": rest,
            "note": note}


GOOD = {
    "rationale": "Push day focus after a solid squat session.",
    "hard": {"title": "Push focus", "exercises": [
        item("dynamic-warmup", 1, seconds=300, rest=0),
        item("knee-push-up", 3, 8, 12, note="Up a step: 3×12 felt easy"),
        item("bodyweight-squat", 3, 12, 15),
        item("plank", 3, seconds=40, rest=45),
        item("static-stretch", 1, seconds=300, rest=0),
    ]},
    "recovery": {"title": "Easy mobility", "day_type": "mobility", "exercises": [
        item("dynamic-warmup", 1, seconds=300, rest=0),
        item("cat-cow", 2, 8, 10, rest=15),
        item("static-stretch", 1, seconds=300, rest=0),
    ]},
    "ladder_overrides": [],
}


class AgentTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.store = Store(tmp / "db")
        self.library = Library(root=tmp / "library")
        self.agent = Agent(self.store, self.library, "notebook")
        profile_mod.save(self.store, {"experience": "beginner", "goals": ["strength"], "equipment": sorted(HOUSE)}, NOW)

    def run_with(self, output):
        with mock.patch.object(Agent, "_claude", return_value=copy.deepcopy(output)):
            return self.agent.run(NOW, HOUSE)

    def test_valid_output_is_stored_and_fresh(self):
        self.assertTrue(self.agent.needs_run())
        plan = self.run_with(GOOD)
        self.assertEqual(plan["hard"]["plan"][1], {"exercise": "knee-push-up", "sets": 3, "rest": 60, "reps": [8, 12],
                                                   "progress": "Up a step: 3×12 felt easy"})
        self.assertEqual(plan["hard"]["plan"][3]["seconds"], 40)
        self.assertFalse(self.agent.needs_run())
        self.assertEqual(self.agent.fresh_plan()["rationale"], GOOD["rationale"])
        self.assertTrue(self.store.get("agent_runs")[-1]["ok"])

    def test_plan_goes_stale_after_a_new_session(self):
        self.run_with(GOOD)
        self.store.db.execute("INSERT INTO sessions (day, finished_at, kind, status) VALUES ('2026-10-05', ?, 'scheduled', 'finished')",
                              (NOW.isoformat(),))
        self.assertIsNone(self.agent.fresh_plan())
        self.assertTrue(self.agent.needs_run())

    def test_rejects_bad_output(self):
        cases = {
            "unknown exercise": lambda o: o["hard"]["exercises"].__setitem__(1, item("burpee-deluxe", 3, 8, 12)),
            "missing equipment": lambda o: o["hard"]["exercises"].__setitem__(1, item("pull-up", 3, 5, 8)),
            "reps on a hold": lambda o: o["hard"]["exercises"].__setitem__(3, item("plank", 3, 8, 12)),
            "too many sets": lambda o: o["hard"]["exercises"].__setitem__(2, item("bodyweight-squat", 9, 12, 15)),
            "override without reason": lambda o: o["ladder_overrides"].append(
                {"chain": "squat", **item("box-squat", 3, 10, 12), "reason": " "}),
            "override on wrong chain": lambda o: o["ladder_overrides"].append(
                {"chain": "core", **item("box-squat", 3, 10, 12), "reason": "knee pain"}),
        }
        for label, mutate in cases.items():
            output = copy.deepcopy(GOOD)
            mutate(output)
            with self.subTest(label), self.assertRaises(AgentError):
                self.run_with(output)
        self.assertIsNone(self.agent.fresh_plan())
        self.assertFalse(self.store.get("agent_runs")[-1]["ok"])

    def test_override_moves_ladder_and_marks_rule_proposal(self):
        self.store.db.execute(
            "INSERT INTO proposals (session_id, chain, rule, from_exercise, from_target, to_exercise, to_target, reason,"
            " status, decided_by, created_at) VALUES (1, 'squat', 'up', 'bodyweight-squat', '{}', 'split-squat', '{}',"
            " 'top of range', 'applied', 'rules', ?)", (NOW.isoformat(),))
        output = copy.deepcopy(GOOD)
        output["ladder_overrides"] = [{"chain": "squat", **item("bodyweight-squat", 3, 12, 15), "reason": "Knee pain in notes"}]
        self.run_with(output)
        ladder = Ladders(self.store, self.library).get("squat")
        self.assertEqual(ladder["exercise"], "bodyweight-squat")
        self.assertEqual(ladder["reason"], "Coach: Knee pain in notes")
        rows = self.store.db.execute("SELECT status, decided_by, override_reason FROM proposals ORDER BY id").fetchall()
        self.assertEqual([tuple(r) for r in rows],
                         [("overridden", "rules", "Knee pain in notes"), ("applied", "agent", None)])

    def test_choose_respects_48_hours(self):
        plan = self.run_with(GOOD)
        self.assertEqual(choose(plan, last_hard=None, now=NOW)["day_type"], "hard")
        self.assertEqual(choose(plan, last_hard=NOW - timedelta(hours=20), now=NOW)["day_type"], "mobility")
        self.assertEqual(choose(plan, last_hard=NOW - timedelta(hours=50), now=NOW)["day_type"], "hard")

    def test_training_serves_generated_plan(self):
        plan = self.run_with(GOOD)
        training = Training(self.store, library=self.library)
        training.begin(now=NOW, kind="scheduled", lock_key="k", minutes=60, equipment=HOUSE, generated=plan)
        snap = training.snapshot()
        self.assertEqual(snap["title"], "Push focus")
        self.assertEqual(snap["source"], "generated")
        self.assertEqual(snap["note"], GOOD["rationale"])
        self.assertEqual(snap["exercises"][1]["name"], "Knee push-up")
        self.assertEqual(snap["exercises"][1]["target"]["progress"], "Up a step: 3×12 felt easy")

    def test_context_has_history_and_catalogue(self):
        context = self.agent.context(NOW, HOUSE)
        self.assertEqual(context["profile"]["experience"], "beginner")
        self.assertEqual(len(context["ladders"]), 8)
        self.assertTrue(any(c["id"] == "pull-up" and not c["available"] for c in context["catalogue"]))
        json.dumps(context)  # must be serialisable for the prompt


class ProfileTest(unittest.TestCase):
    def test_presets_use_real_exercises_on_their_chains(self):
        seed = load_seed()
        for level, preset in profile_mod.PRESETS.items():
            for chain, (exercise, _) in preset.items():
                with self.subTest(level=level, chain=chain):
                    self.assertEqual(seed[exercise]["chain"], chain)

    def test_first_save_seeds_ladders_but_later_edits_do_not(self):
        store = Store(Path(tempfile.mkdtemp()) / "db")
        library = Library(root=Path(tempfile.mkdtemp()))
        profile_mod.save(store, {"experience": "intermediate", "goals": ["muscle"]}, NOW)
        self.assertEqual(Ladders(store, library).get("push-horizontal")["exercise"], "push-up")
        store.db.execute("UPDATE ladders SET exercise = 'diamond-push-up' WHERE chain = 'push-horizontal'")
        profile_mod.save(store, {"experience": "advanced", "goals": ["muscle"]}, NOW)
        self.assertEqual(Ladders(store, library).get("push-horizontal")["exercise"], "diamond-push-up")

    def test_validation(self):
        with self.assertRaises(profile_mod.ProfileError):
            profile_mod.validate({"experience": "beginner", "goals": []})
        with self.assertRaises(profile_mod.ProfileError):
            profile_mod.validate({"experience": "beginner", "goals": ["strength"], "age": "abc"})
        p = profile_mod.validate({"experience": "beginner", "goals": ["strength", "flying"], "equipment": ["bar", "jetpack"]})
        self.assertEqual((p["goals"], p["equipment"]), (["strength"], ["bar"]))


if __name__ == "__main__":
    unittest.main()

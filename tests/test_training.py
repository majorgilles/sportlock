import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from sportlock.library import Library, load_seed, stick_figure
from sportlock.store import Store
from sportlock.ladders import START, Ladders
from sportlock.training import Training, TrainingError, estimate_seconds, fit_plan

T0 = datetime(2026, 10, 5, 18, 0, 0)
HOUSEHOLD = {"chair", "table", "bench", "doorway"}


def fresh_plan():
    tmp = Path(tempfile.mkdtemp())
    return Ladders(Store(tmp / "db"), Library(root=tmp / "library")).plan(T0, HOUSEHOLD)["plan"]


class FitPlanTest(unittest.TestCase):
    def test_full_plan_fits_long_lock(self):
        plan = fresh_plan()
        self.assertEqual(fit_plan(plan, 120), plan)

    def test_shrinks_but_keeps_warmup_and_cooldown(self):
        plan = fresh_plan()
        fitted = fit_plan(plan, 15)
        self.assertLessEqual(estimate_seconds(fitted), 15 * 60)
        self.assertEqual(fitted[0]["exercise"], "dynamic-warmup")
        self.assertEqual(fitted[-1]["exercise"], "static-stretch")
        self.assertGreaterEqual(len(fitted), 3)

    def test_does_not_mutate_input(self):
        plan = fresh_plan()
        before = [dict(item) for item in plan]
        fit_plan(plan, 10)
        self.assertEqual(plan, before)


class TrainingTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.store = Store(tmp / "db.sqlite")
        self.training = Training(self.store, library=Library(root=tmp / "library"))
        self.training.begin(now=T0, kind="scheduled", lock_key="2026-10-05T18:00", minutes=60)

    def t(self, seconds):
        return T0 + timedelta(seconds=seconds)

    def do_timed(self, start, length):
        self.training.start_set(now=self.t(start))
        self.training.stop_set(now=self.t(start + length))
        self.training.save_set(now=self.t(start + length + 1))
        self.training.rate(now=self.t(start + length + 5), rpe=3)

    def test_timed_exercise_records_measured_duration(self):
        self.do_timed(0, 290)
        sets = self.store.db.execute("SELECT * FROM sets").fetchall()
        self.assertEqual(sets[0]["seconds"], 290)
        self.assertIsNone(sets[0]["reps"])
        self.assertEqual(self.training.run["current"], 1)

    def test_reps_sets_rest_and_rating(self):
        self.do_timed(0, 300)
        tr = self.training
        tr.start_set(now=self.t(310))
        tr.stop_set(now=self.t(340))
        with self.assertRaises(TrainingError):
            tr.save_set(now=self.t(341))  # reps required
        tr.save_set(now=self.t(341), reps=10, load_kg=None)
        self.assertEqual(tr.run["phase"], "resting")
        tr.start_set(now=self.t(400))  # rested 60 s since the set ended at 340
        tr.stop_set(now=self.t(425))
        tr.save_set(now=self.t(426), reps=9)
        tr.start_set(now=self.t(490))
        tr.stop_set(now=self.t(512))
        tr.save_set(now=self.t(513), reps=8)
        self.assertEqual(tr.run["phase"], "rating")
        tr.rate(now=self.t(520), rpe=7, note="last set hard")

        rows = self.store.db.execute("SELECT reps, seconds, rest_seconds FROM sets WHERE set_no > 0 ORDER BY id").fetchall()
        self.assertEqual([tuple(r) for r in rows[1:]], [(10, 30, None), (9, 25, 60), (8, 22, 65)])
        ex = self.store.db.execute("SELECT status, rpe, note FROM session_exercises WHERE name = 'Incline push-up'").fetchone()
        self.assertEqual(tuple(ex), ("done", 7, "last set hard"))

    def test_swap_to_easier_variation(self):
        self.do_timed(0, 300)
        self.training.swap_easier(now=self.t(305))
        snap = self.training.snapshot()
        self.assertEqual(snap["exercises"][1]["status"], "swapped")
        self.assertEqual(snap["exercises"][2]["name"], "Wall push-up")
        self.assertEqual(snap["current"], 2)

    def test_skip_needs_reason(self):
        with self.assertRaises(TrainingError):
            self.training.skip(now=self.t(1), reason="")
        self.training.skip(now=self.t(1), reason="shoulder pain")
        row = self.store.db.execute("SELECT status, skip_reason FROM session_exercises ORDER BY id").fetchone()
        self.assertEqual(tuple(row), ("skipped", "shoulder pain"))

    def test_finish_session(self):
        for _ in range(len(self.training.run["order"])):
            self.training.skip(now=self.t(1), reason="testing")
        self.assertEqual(self.training.run["phase"], "summary")
        self.training.finish(now=self.t(60), rpe=6, notes="ok", calories=200, avg_hr=120, body_weight=78.5)
        self.assertIsNone(self.training.run)
        row = self.store.db.execute("SELECT status, rpe, calories, avg_hr, body_weight FROM sessions").fetchone()
        self.assertEqual(tuple(row), ("finished", 6, 200, 120, 78.5))
        self.assertIn(T0.date(), self.store.trained_days())

    def test_abandoned_session_does_not_count(self):
        self.training.close(now=self.t(60), status="abandoned")
        self.assertEqual(self.store.trained_days(), set())

    def test_snapshot_shows_running_timer(self):
        self.training.start_set(now=self.t(5))
        snap = self.training.snapshot()
        self.assertEqual(snap["phase"], "running")
        self.assertEqual(snap["set_started_at"], int(self.t(5).timestamp() * 1000))


class LibraryTest(unittest.TestCase):
    def test_seed_chains_link_easier_and_harder(self):
        seed = load_seed()
        self.assertEqual(seed["incline-push-up"]["easier"], "wall-push-up")
        self.assertEqual(seed["incline-push-up"]["harder"], "knee-push-up")
        self.assertIsNone(seed["wall-push-up"]["easier"])

    def test_ladder_starts_are_on_their_chains(self):
        seed = load_seed()
        for chain, (exercise, _) in START.items():
            self.assertEqual(seed[exercise]["chain"], chain)

    def test_built_details_merge_over_seed(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "plank").mkdir()
        (tmp / "plank" / "exercise.json").write_text('{"steps": ["a", "b"], "cues": ["x"], "image": "picture.jpg", "image_source": "book: B"}')
        entry = Library(root=tmp).get("plank")
        self.assertEqual(entry["steps"], ["a", "b"])
        self.assertEqual(entry["cues"], ["x"])
        self.assertEqual(entry["image"], str(tmp / "plank" / "picture.jpg"))
        self.assertEqual(entry["pattern"], "core")

    def test_unbuilt_exercise_keeps_seed_cues(self):
        entry = Library(root=Path(tempfile.mkdtemp())).get("plank")
        self.assertEqual(len(entry["cues"]), 3)
        self.assertEqual(entry["image"], "")

    def test_stick_figures_are_svg(self):
        for pattern in ("push", "pull", "squat", "hinge", "core", "mobility", "warmup", "unknown"):
            self.assertTrue(stick_figure(pattern).startswith("<svg"))


if __name__ == "__main__":
    unittest.main()

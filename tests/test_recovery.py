import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from sportlock import recovery
from sportlock.library import Library
from sportlock.recovery import Policy, plan_lock
from sportlock.store import Store
from sportlock.training import Training, estimate_seconds, fit_plan

NOW = datetime(2026, 10, 6, 18, 0)  # Tuesday
HOUSE = {"chair", "table", "bench", "doorway"}


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.store = Store(tmp / "db")
        self.library = Library(root=tmp / "library")

    def session(self, finished, minutes=45, rpe=8, day_type="hard", status="finished", kind="scheduled"):
        started = finished - timedelta(minutes=minutes)
        self.store.db.execute(
            "INSERT INTO sessions (day, started_at, finished_at, kind, status, day_type, rpe) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (finished.date().isoformat(), started.isoformat(), finished.isoformat(), kind, status, day_type, rpe))

    def rest_day(self, day):
        key = f"{day.isoformat()}T18:00"
        self.store.db.execute("INSERT INTO lock_events (key, start, end, began_at, ended_at, outcome)"
                              " VALUES (?, ?, ?, ?, ?, 'rest')", (key, key, key, key, key))

    def plan(self, coach=None, policy=Policy(), minutes=30):
        return plan_lock(self.store, policy, now=NOW, window_minutes=minutes, coach=coach)

    # -- decisions -------------------------------------------------------------------------------

    def test_no_history_means_hard(self):
        self.assertEqual(self.plan().mode, "hard")

    def test_auto_recovery_within_48h_of_hard(self):
        self.session(NOW - timedelta(hours=24))
        p = self.plan()
        self.assertEqual((p.mode, p.minutes), ("recovery", 15))  # default recovery length

    def test_coach_rest_after_big_session_when_allowed(self):
        for days in (1, 3, 5):
            self.session(NOW - timedelta(days=days, hours=1 if days == 1 else 0))
        p = self.plan({"mode": "rest", "recovery_minutes": None, "reason": "45 min at effort 8 yesterday"})
        self.assertEqual((p.mode, p.minutes, p.reason), ("rest", 0, "45 min at effort 8 yesterday"))

    def test_rest_needs_enough_sessions_this_week(self):
        self.session(NOW - timedelta(hours=20))
        p = self.plan({"mode": "rest", "recovery_minutes": 10, "reason": "tired"})
        self.assertEqual((p.mode, p.minutes), ("recovery", 10))
        self.assertIn("fewer than 3 sessions", p.reason)

    def test_rest_needs_a_recent_session(self):
        for days in (2, 3, 4):
            self.session(NOW - timedelta(days=days))
        self.assertEqual(self.plan({"mode": "rest", "recovery_minutes": None, "reason": ""}).mode, "recovery")

    def test_no_more_than_two_rest_days_in_a_row(self):
        for days in (1, 4, 5):
            self.session(NOW - timedelta(days=days, hours=-1))
        self.rest_day(NOW.date() - timedelta(days=1))
        self.rest_day(NOW.date() - timedelta(days=2))
        p = self.plan({"mode": "rest", "recovery_minutes": None, "reason": ""})
        self.assertEqual(p.mode, "recovery")
        self.assertIn("2 rest days in a row", p.reason)

    def test_rest_days_off_in_settings(self):
        for days in (1, 3, 5):
            self.session(NOW - timedelta(days=days, hours=-1))
        p = self.plan({"mode": "rest", "recovery_minutes": None, "reason": ""}, Policy(allow_rest_days=False))
        self.assertEqual(p.mode, "recovery")

    def test_recovery_never_longer_than_the_lock(self):
        self.session(NOW - timedelta(hours=24))
        self.assertEqual(self.plan({"mode": "recovery", "recovery_minutes": 40, "reason": ""}, minutes=20).minutes, 20)

    def test_test_sessions_never_count(self):
        self.session(NOW - timedelta(hours=10), kind="test")
        self.assertEqual(self.plan().mode, "hard")

    # -- load and pace ---------------------------------------------------------------------------

    def test_load_summary(self):
        self.session(NOW - timedelta(hours=24), minutes=45, rpe=8)
        summary = recovery.load_summary(self.store, NOW)
        self.assertEqual((summary["sessions_last_7_days"], summary["minutes_last_7_days"], summary["load_last_7_days"]),
                         (1, 45, 360))

    def test_default_pace_and_transition_without_history(self):
        self.assertEqual(recovery.pace_factor(self.store), recovery.DEFAULT_PACE)
        self.assertEqual(recovery.transition_seconds(self.store), recovery.DEFAULT_TRANSITION)

    def test_plan_fits_with_transitions(self):
        plan = [{"exercise": "a", "sets": 1, "seconds": 120, "rest": 0},
                *[{"exercise": f"m{i}", "sets": 3, "reps": [8, 12], "rest": 60} for i in range(4)],
                {"exercise": "z", "sets": 1, "seconds": 120, "rest": 0}]
        fitted = fit_plan(plan, 20, pace=1.0, transition=40)
        self.assertLessEqual(estimate_seconds(fitted, pace=1.0, transition=40), 20 * 60)
        self.assertLess(len(fitted) + sum(i["sets"] for i in fitted), len(plan) + sum(i["sets"] for i in plan))


class CreditTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.store = Store(tmp / "db")
        self.training = Training(self.store, library=Library(root=tmp / "library"))

    def run_until_cooldown(self, skip_one=False):
        tr, t = self.training, NOW
        tr.begin(now=t, kind="scheduled", lock_key="k", minutes=60, equipment=HOUSE)
        order = tr.run["order"]
        for index in range(len(order) - 1):
            if skip_one and index == 2:
                tr.skip(now=t, reason="shoulder")
                continue
            tr.start_set(now=t)
            row = tr._current(tr.run)
            for _ in range(json.loads(row["target"])["sets"]):
                if tr.run["phase"] in ("ready", "resting"):
                    tr.start_set(now=t)
                tr.stop_set(now=t + timedelta(seconds=30))
                tr.save_set(now=t + timedelta(seconds=31), reps=10)
            tr.rate(now=t, rpe=6)
        tr.close(now=t + timedelta(minutes=20), status="abandoned")
        return self.store.db.execute("SELECT status, notes FROM sessions").fetchone()

    def test_time_running_out_in_the_cooldown_counts(self):
        status, notes = self.run_until_cooldown()
        self.assertEqual(status, "finished")
        self.assertIn("main work", notes)
        self.assertIn(NOW.date(), self.store.trained_days())

    def test_skipped_main_work_does_not_count(self):
        status, _ = self.run_until_cooldown(skip_one=True)
        self.assertEqual(status, "abandoned")


if __name__ == "__main__":
    unittest.main()

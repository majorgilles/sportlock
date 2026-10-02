import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from sportlock import diagnostics
from sportlock.library import Library
from sportlock.store import Store

NOW = datetime(2026, 10, 5, 18, 0)


class DoctorTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.store = Store(tmp / "db")
        self.library = Library(root=tmp / "library")
        self.store.put("profile", {"experience": "beginner"})
        db = self.store.db
        db.execute("INSERT INTO sessions (id, day, started_at, finished_at, kind, status) VALUES"
                   " (1, '2026-10-05', ?, ?, 'test', 'abandoned')", (NOW.isoformat(), NOW.isoformat()))

    def issues(self, lock_active=False):
        return diagnostics.check(self.store, self.library, lock_active=lock_active)

    def errors(self, issues):
        return [i for i in issues if i.severity == "error"]

    def test_clean_database_has_no_errors(self):
        self.assertEqual(self.errors(self.issues()), [])

    def test_timed_exercise_with_rep_target_is_found_and_repaired(self):
        # What a "Too hard" swap from cat-cow to the cool-down produced before the fix.
        self.store.db.execute(
            "INSERT INTO session_exercises (session_id, exercise, name, pattern, kind, target, status)"
            " VALUES (1, 'static-stretch', 'Cool-down stretching', 'mobility', 'timed', ?, 'done')",
            (json.dumps({"sets": 2, "rest": 15, "reps": [8, 10]}),))
        issues = self.issues()
        self.assertEqual(len(self.errors(issues)), 1)
        self.assertEqual(diagnostics.repair(issues), 1)
        target = json.loads(self.store.db.execute("SELECT target FROM session_exercises").fetchone()[0])
        self.assertEqual(target, {"sets": 2, "rest": 15, "seconds": 120})
        self.assertEqual(self.errors(self.issues()), [])

    def test_stuck_in_progress_session_is_closed(self):
        self.store.db.execute("INSERT INTO sessions (id, day, finished_at, kind, status) VALUES (2, '2026-10-05', ?, 'scheduled', 'in_progress')",
                              (NOW.isoformat(),))
        diagnostics.repair(self.issues())
        status = self.store.db.execute("SELECT status FROM sessions WHERE id = 2").fetchone()[0]
        self.assertEqual(status, "abandoned")

    def test_leftover_run_kept_during_a_lock_but_cleared_otherwise(self):
        self.store.put("training", {"session_id": 99})
        during = self.issues(lock_active=True)
        diagnostics.repair(during)
        self.assertIsNotNone(self.store.get("training"))
        diagnostics.repair(self.issues())
        self.assertIsNone(self.store.get("training"))

    def test_bad_ladder_is_reset(self):
        self.store.db.execute("INSERT INTO ladders VALUES ('squat', 'plank', '{}', NULL, ?)", (NOW.isoformat(),))
        diagnostics.repair(self.issues())
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM ladders").fetchone()[0], 0)

    def test_report_is_written(self):
        path = diagnostics.build_report(self.store, self.library, state={"locked": False}, issues=self.issues(),
                                        note="lock screen froze")
        text = path.read_text()
        self.assertIn("lock screen froze", text)
        self.assertIn("## Doctor", text)


if __name__ == "__main__":
    unittest.main()

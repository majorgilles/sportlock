"""`sportlock doctor`: consistency checks over the data, with optional repairs."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from sportlock.exercises.domain.catalogue import Catalogue
from sportlock.exercises.infrastructure.details_repository import FileExerciseDetailsRepository
from sportlock.progression.domain.ladders import START
from sportlock.shared_kernel.infrastructure.database import Database
from sportlock.shared_kernel.targets import Target
from sportlock.training.infrastructure.sqlite_repositories import RUN_KEY

REP_RANGE_SIZE = 2  # low and high


@dataclass
class Issue:
    """One problem found, and its repair if there is one."""

    severity: str  # error | warning | info
    what: str
    fix: str | None = None  # description of the repair `--fix` applies, if any
    _apply: object = None

    def as_dict(self) -> dict:
        """The JSON shape the CLI prints."""
        return {"severity": self.severity, "what": self.what, "fix": self.fix}


def _target_problem(kind: str, target: dict) -> str | None:
    sets = target.get("sets")
    if not isinstance(sets, int) or sets < 1:
        return f"sets is {sets!r}"
    if kind == "reps":
        reps = target.get("reps")
        if not (
            isinstance(reps, list)
            and len(reps) == REP_RANGE_SIZE
            and all(isinstance(r, int) for r in reps)
            and reps[0] <= reps[1]
        ):
            return f"a reps exercise without a valid rep range ({target})"
    else:
        seconds = target.get("seconds")
        if not isinstance(seconds, (int, float)) or seconds <= 0:
            return f"a {kind} exercise without a duration ({target})"
    return None


def _target_issues(db: sqlite3.Connection) -> list[Issue]:
    """Exercise targets that don't match the exercise's kind (e.g. a swap that kept a rep range)."""
    issues = []
    for row in db.execute("SELECT id, session_id, exercise, name, kind, target FROM session_exercises"):
        target = json.loads(row["target"])
        problem = _target_problem(row["kind"], target)
        if problem:

            def repair(row: sqlite3.Row = row, target: dict = target) -> None:
                db.execute(
                    "UPDATE session_exercises SET target = ? WHERE id = ?",
                    (json.dumps(Target.from_dict(target).for_kind(row["kind"]).to_dict()), row["id"]),
                )

            issues.append(
                Issue(
                    "error",
                    f"session {row['session_id']}: {row['name']} has {problem}",
                    "convert the target to the exercise's kind",
                    repair,
                )
            )
    return issues


def _set_issues(db: sqlite3.Connection) -> list[Issue]:
    """Sets that can't be right."""
    issues = []
    for row in db.execute(
        "SELECT s.id, s.reps, s.seconds, e.name, e.kind, e.session_id FROM sets s"
        " JOIN session_exercises e ON e.id = s.session_exercise_id"
    ):
        if row["seconds"] is None or row["seconds"] < 0:
            issues.append(
                Issue("error", f"session {row['session_id']}: a set of {row['name']} has duration {row['seconds']!r}")
            )
        if row["kind"] == "reps" and row["reps"] is None:
            issues.append(
                Issue(
                    "warning",
                    f"session {row['session_id']}: a set of {row['name']} has no reps "
                    "(logged while the exercise was set up wrongly)",
                )
            )
    return issues


def _stuck_session_issues(db: sqlite3.Connection, run: dict | None) -> list[Issue]:
    """Sessions stuck "in progress" with nothing running them."""
    issues = []
    for row in db.execute("SELECT id, started_at FROM sessions WHERE status = 'in_progress'"):
        if run and run.get("session_id") == row["id"]:
            continue

        def repair(row: sqlite3.Row = row) -> None:
            db.execute("UPDATE sessions SET status = 'abandoned' WHERE id = ?", (row["id"],))

        issues.append(
            Issue(
                "warning",
                f"session {row['id']} (started {row['started_at']}) is still marked in progress",
                "mark it abandoned",
                repair,
            )
        )
    return issues


def _leftover_run_issues(database: Database, run: dict | None, *, lock_active: bool) -> list[Issue]:
    """A live training run that points at nothing."""
    if not run:
        return []
    session = database.db.execute("SELECT status FROM sessions WHERE id = ?", (run.get("session_id"),)).fetchone()
    if session is not None and session["status"] == "in_progress":
        return []
    if lock_active:
        return [Issue("error", "the running session's record is missing or closed (not repaired during a lock)")]
    return [
        Issue(
            "error",
            "a leftover training run points at a closed or missing session",
            "discard the leftover run",
            lambda: database.delete(RUN_KEY),
        )
    ]


def _ladder_problem(catalogue: Catalogue, row: sqlite3.Row) -> str | None:
    spec = catalogue.find(row["exercise"])
    if row["chain"] not in START:
        return "is not a known chain"
    if spec is None:
        return f"points at unknown exercise {row['exercise']!r}"
    if spec.chain != row["chain"]:
        return f"points at {row['exercise']}, which belongs to {spec.chain}"
    return _target_problem(spec.kind, json.loads(row["target"]))


def _ladder_issues(db: sqlite3.Connection, catalogue: Catalogue) -> list[Issue]:
    """Ladders that point at unknown exercises, the wrong chain, or a bad target."""
    issues = []
    for row in db.execute("SELECT chain, exercise, target FROM ladders"):
        problem = _ladder_problem(catalogue, row)
        if problem:

            def repair(chain: str = row["chain"]) -> None:
                db.execute("DELETE FROM ladders WHERE chain = ?", (chain,))

            issues.append(
                Issue("error", f"ladder {row['chain']} {problem}", "reset that chain to its starting point", repair)
            )
    return issues


def _open_lock_issues(db: sqlite3.Connection, *, lock_active: bool) -> list[Issue]:
    """Lock records left open while no lock is active."""
    if lock_active:
        return []
    issues = []
    for row in db.execute("SELECT key FROM lock_events WHERE ended_at IS NULL"):

        def repair(key: str = row["key"]) -> None:
            db.execute("UPDATE lock_events SET ended_at = end, outcome = 'expired' WHERE key = ?", (key,))

        issues.append(
            Issue(
                "warning",
                f"lock {row['key']} was never closed (service stopped mid-lock?)",
                "close it as expired",
                repair,
            )
        )
    return issues


def _library_issues(catalogue: Catalogue, details: FileExerciseDetailsRepository, library_status: dict) -> list[Issue]:
    """Library files."""
    issues = []
    for exercise in catalogue.all():
        entry = details.get_by_id(exercise.id)
        if entry.built and entry.image and not Path(entry.image).exists():
            issues.append(Issue("warning", f"library: {exercise.name} picture file is missing"))
    missing = library_status["missing"]
    if missing:
        issues.append(Issue("info", f"library: {len(missing)} exercises not built yet (run `sportlock library build`)"))
    return issues


def _setup_issues(database: Database) -> list[Issue]:
    """Setup and agent."""
    issues = []
    if database.get("profile") is None:
        issues.append(Issue("info", "no profile yet: scheduled locks are off until you run `sportlock app`"))
    last_run = database.db.execute("SELECT * FROM coach_runs ORDER BY id DESC LIMIT 1").fetchone()
    if last_run and not last_run["ok"]:
        issues.append(Issue("warning", f"last coach run failed at {last_run['at']}: {last_run['error']}"))
    return issues


def check(
    database: Database,
    catalogue: Catalogue,
    details: FileExerciseDetailsRepository,
    library_status: dict,
    *,
    lock_active: bool,
) -> list[Issue]:
    """Consistency checks over the data; repairs are attached, never applied here."""
    db = database.db
    run = database.get(RUN_KEY)
    return [
        *_target_issues(db),
        *_set_issues(db),
        *_stuck_session_issues(db, run),
        *_leftover_run_issues(database, run, lock_active=lock_active),
        *_ladder_issues(db, catalogue),
        *_open_lock_issues(db, lock_active=lock_active),
        *_library_issues(catalogue, details, library_status),
        *_setup_issues(database),
    ]


def repair(issues: list[Issue]) -> int:
    """Apply every attached repair; returns how many."""
    fixed = 0
    for issue in issues:
        if issue._apply:
            issue._apply()
            fixed += 1
    return fixed

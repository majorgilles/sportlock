"""Training sessions: the plan served during a lock and everything logged while doing it.

A session moves through phases per exercise:

    ready ──start──▶ running ──stop──▶ logging ──save──▶ resting ──start──▶ running …
                                                   └─(target sets done)──▶ rating ──rate──▶ next exercise
    after the last exercise: summary ──finish──▶ (session finished, lock ends)

Set durations are measured from Start to Stop; rest is the gap since the previous set ended.
The live position (phase, timers) is kept in the store's kv table so a restarted service or
locker resumes exactly where you were.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from importlib import resources

from .library import Library
from .store import Store, _iso

RUN_KEY = "training"
SECONDS_PER_REP = 3
MIN_TIMED_SECONDS = 120


class TrainingError(ValueError):
    pass


def load_starter() -> dict:
    return json.loads(resources.files("sportlock").joinpath("data/starter.json").read_text())


def _work_seconds(item: dict) -> int:
    return item["reps"][1] * SECONDS_PER_REP if "reps" in item else item["seconds"]


def estimate_seconds(plan: list[dict]) -> int:
    return sum(item["sets"] * (_work_seconds(item) + item["rest"]) for item in plan)


def fit_plan(plan: list[dict], minutes: float) -> list[dict]:
    """Shrink the plan to fit the lock: fewer sets first, then shorter warm-up/cool-down,
    then drop main exercises from the end. Warm-up and cool-down (first/last) always stay."""
    plan = [dict(item) for item in plan]
    budget = minutes * 60

    def main_items():
        return plan[1:-1] if len(plan) > 2 else []

    while estimate_seconds(plan) > budget:
        reducible = [item for item in main_items() if item["sets"] > 2]
        if reducible:
            max(reducible, key=lambda item: item["sets"])["sets"] -= 1
            continue
        timed = [item for item in (plan[0], plan[-1]) if "seconds" in item and item["sets"] == 1
                 and item["seconds"] > MIN_TIMED_SECONDS]
        if timed:
            for item in timed:
                item["seconds"] = max(MIN_TIMED_SECONDS, item["seconds"] - 60)
            continue
        if len(main_items()) > 1:
            plan.pop(-2)
            continue
        break
    return plan


def _ms(moment: str | None) -> int | None:
    return int(datetime.fromisoformat(moment).timestamp() * 1000) if moment else None


class Training:
    def __init__(self, store: Store, starter: dict | None = None, library: Library | None = None):
        self.store = store
        self.starter = starter or load_starter()
        self.library = library or Library()

    # -- run state -----------------------------------------------------------------------------

    @property
    def run(self) -> dict | None:
        return self.store.get(RUN_KEY)

    def _save(self, run: dict) -> None:
        self.store.put(RUN_KEY, run)

    def _require(self, *phases: str) -> dict:
        run = self.run
        if run is None:
            raise TrainingError("no session in progress")
        if phases and run["phase"] not in phases:
            raise TrainingError(f"can't do that while {run['phase']}")
        return run

    def _row(self, row_id: int) -> dict:
        return dict(self.store.db.execute("SELECT * FROM session_exercises WHERE id = ?", (row_id,)).fetchone())

    def _sets(self, row_id: int) -> list[dict]:
        rows = self.store.db.execute("SELECT * FROM sets WHERE session_exercise_id = ? ORDER BY set_no", (row_id,))
        return [dict(row) for row in rows]

    def _current(self, run: dict) -> dict:
        return self._row(run["order"][run["current"]])

    def _insert_exercise(self, session_id: int, exercise_id: str, target: dict) -> int:
        spec = self.library.get(exercise_id)
        cursor = self.store.db.execute(
            "INSERT INTO session_exercises (session_id, exercise, name, pattern, kind, target) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, exercise_id, spec["name"], spec["pattern"], spec["kind"], json.dumps(target)),
        )
        return cursor.lastrowid

    # -- lifecycle -----------------------------------------------------------------------------

    def begin(self, *, now: datetime, kind: str, lock_key: str | None, minutes: float) -> None:
        if self.run is not None:
            return
        plan = fit_plan(self.starter["plan"], minutes)
        # finished_at is NOT NULL from the first schema; it is rewritten when the session closes.
        cursor = self.store.db.execute(
            "INSERT INTO sessions (day, started_at, finished_at, kind, lock_key, status, title, day_type, plan_source)"
            " VALUES (?, ?, ?, ?, ?, 'in_progress', ?, ?, 'starter')",
            (now.date().isoformat(), _iso(now), _iso(now), kind, lock_key, self.starter["title"], self.starter["day_type"]),
        )
        session_id = cursor.lastrowid
        order = [self._insert_exercise(session_id, item["exercise"], {k: v for k, v in item.items() if k != "exercise"})
                 for item in plan]
        self._save({"session_id": session_id, "order": order, "current": 0, "phase": "ready",
                    "set_started_at": None, "set_ended_at": None, "last_set_end": None, "rest_until": None})

    def close(self, *, now: datetime, status: str) -> None:
        """End an unfinished session (abandoned when time ran out, overridden)."""
        run = self.run
        if run is None:
            return
        self.store.db.execute("UPDATE sessions SET status = ?, finished_at = ? WHERE id = ?",
                              (status, _iso(now), run["session_id"]))
        self.store.delete(RUN_KEY)

    def finish(self, *, now: datetime, rpe: int, notes: str = "", calories: int | None = None,
               avg_hr: int | None = None, body_weight: float | None = None) -> int:
        run = self._require("summary")
        if not 1 <= int(rpe) <= 10:
            raise TrainingError("effort must be between 1 and 10")
        self.store.db.execute(
            "UPDATE sessions SET status = 'finished', finished_at = ?, rpe = ?, notes = ?, calories = ?, avg_hr = ?,"
            " body_weight = ? WHERE id = ?",
            (_iso(now), int(rpe), notes, calories, avg_hr, body_weight, run["session_id"]),
        )
        self.store.delete(RUN_KEY)
        return run["session_id"]

    # -- sets ----------------------------------------------------------------------------------

    def start_set(self, *, now: datetime) -> None:
        run = self._require("ready", "resting")
        row = self._current(run)
        if row["started_at"] is None:
            self.store.db.execute("UPDATE session_exercises SET started_at = ? WHERE id = ?", (_iso(now), row["id"]))
        run.update(phase="running", set_started_at=_iso(now), rest_until=None)
        self._save(run)

    def stop_set(self, *, now: datetime) -> None:
        run = self._require("running")
        run.update(phase="logging", set_ended_at=_iso(now))
        self._save(run)

    def save_set(self, *, now: datetime, reps: int | None = None, load_kg: float | None = None) -> None:
        run = self._require("logging")
        row = self._current(run)
        target = json.loads(row["target"])
        if row["kind"] == "reps" and (reps is None or int(reps) < 0):
            raise TrainingError("enter the reps you did")

        started = datetime.fromisoformat(run["set_started_at"])
        ended = datetime.fromisoformat(run["set_ended_at"])
        last_end = run["last_set_end"] and datetime.fromisoformat(run["last_set_end"])
        done = self._sets(row["id"])
        self.store.db.execute(
            "INSERT INTO sets (session_exercise_id, set_no, reps, seconds, load_kg, rest_seconds, started_at, ended_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (row["id"], len(done) + 1, None if row["kind"] != "reps" else int(reps), (ended - started).total_seconds(),
             load_kg, last_end and (started - last_end).total_seconds(), _iso(started), _iso(ended)),
        )

        if len(done) + 1 >= target["sets"]:
            run.update(phase="rating", last_set_end=_iso(ended))
        else:
            run.update(phase="resting", last_set_end=_iso(ended),
                       rest_until=_iso(now + timedelta(seconds=target["rest"])))
        self._save(run)

    def end_sets(self, *, now: datetime) -> None:
        """Stop the exercise early (fewer sets than planned) and go rate it."""
        run = self._require("ready", "resting")
        if not self._sets(self._current(run)["id"]):
            raise TrainingError("log at least one set, or skip the exercise")
        run.update(phase="rating", rest_until=None)
        self._save(run)

    # -- exercises -----------------------------------------------------------------------------

    def rate(self, *, now: datetime, rpe: int, note: str = "") -> None:
        run = self._require("rating")
        if not 1 <= int(rpe) <= 10:
            raise TrainingError("effort must be between 1 and 10")
        self.store.db.execute(
            "UPDATE session_exercises SET status = 'done', rpe = ?, note = ?, ended_at = ? WHERE id = ?",
            (int(rpe), note, _iso(now), self._current(run)["id"]),
        )
        self._advance(run)

    def skip(self, *, now: datetime, reason: str) -> None:
        run = self._require("ready", "running", "logging", "resting", "rating")
        if len(reason.strip()) < 3:
            raise TrainingError("give a reason for skipping")
        self.store.db.execute(
            "UPDATE session_exercises SET status = 'skipped', skip_reason = ?, ended_at = ? WHERE id = ?",
            (reason.strip(), _iso(now), self._current(run)["id"]),
        )
        self._advance(run)

    def swap_easier(self, *, now: datetime) -> None:
        run = self._require("ready", "resting", "running")
        row = self._current(run)
        easier = self.library.get(row["exercise"]).get("easier")
        if not easier:
            raise TrainingError("no easier variation for this exercise")
        new_id = self._insert_exercise(run["session_id"], easier, json.loads(row["target"]))
        self.store.db.execute(
            "UPDATE session_exercises SET status = 'swapped', swapped_to = ?, ended_at = ? WHERE id = ?",
            (new_id, _iso(now), row["id"]),
        )
        run["order"].insert(run["current"] + 1, new_id)
        self._advance(run)

    def _advance(self, run: dict) -> None:
        run["current"] += 1
        run.update(phase="summary" if run["current"] >= len(run["order"]) else "ready",
                   set_started_at=None, set_ended_at=None, last_set_end=None, rest_until=None)
        self._save(run)

    # -- state for the locker ------------------------------------------------------------------

    def snapshot(self) -> dict | None:
        run = self.run
        if run is None:
            return None
        session = dict(self.store.db.execute("SELECT * FROM sessions WHERE id = ?", (run["session_id"],)).fetchone())
        exercises = []
        for row_id in run["order"]:
            row = self._row(row_id)
            spec = self.library.get(row["exercise"])
            exercises.append({
                "name": row["name"], "pattern": row["pattern"], "kind": row["kind"],
                "target": json.loads(row["target"]), "cues": spec.get("cues", []),
                "steps": spec.get("steps", []), "mistakes": spec.get("mistakes", []),
                "breathing": spec.get("breathing") or "", "sources": spec.get("sources", []),
                "image": spec.get("image", ""), "image_source": spec.get("image_source", ""),
                "easier_name": self.library.get(spec["easier"])["name"] if spec.get("easier") else "",
                "harder_name": self.library.get(spec["harder"])["name"] if spec.get("harder") else "",
                "has_easier": bool(spec.get("easier")), "status": row["status"], "rpe": row["rpe"],
                "sets": [{"reps": s["reps"], "seconds": s["seconds"], "load_kg": s["load_kg"]} for s in self._sets(row_id)],
            })
        pending = None
        if run["phase"] == "logging":
            pending = (datetime.fromisoformat(run["set_ended_at"]) - datetime.fromisoformat(run["set_started_at"])).total_seconds()
        return {
            "id": run["session_id"], "title": session["title"], "day_type": session["day_type"], "kind": session["kind"],
            "phase": run["phase"], "current": run["current"], "exercises": exercises,
            "set_started_at": _ms(run["set_started_at"]), "rest_until": _ms(run["rest_until"]),
            "pending_seconds": pending,
        }

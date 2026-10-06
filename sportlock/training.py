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

from .ladders import START, Ladders
from .library import Library
from .store import Store, _iso

RUN_KEY = "training"
LADDERED_CHAINS = set(START)
DEFAULT_SECONDS = {"hold": 30, "timed": 120}
SECONDS_PER_REP = 3
MIN_TIMED_SECONDS = 120


class TrainingError(ValueError):
    pass


def _work_seconds(item: dict) -> int:
    work = item["reps"][1] * SECONDS_PER_REP if "reps" in item else item["seconds"]
    return work * 2 if item.get("sides") else work  # reps and seconds count per side


def _estimate(plan: list[dict], *, pace: float = 1.0, transition: int = 0) -> int:
    """Expected session length: sets × (work + rest) scaled by the user's measured pace, plus the
    measured gap between exercises."""
    work = sum(item["sets"] * (_work_seconds(item) + item["rest"]) for item in plan)
    return int(work * pace + transition * len(plan))


estimate_seconds = _estimate


def fit_plan(plan: list[dict], minutes: float, *, pace: float = 1.0, transition: int = 0) -> list[dict]:
    """Shrink the plan to fit the lock: fewer sets first, then shorter warm-up/cool-down,
    then drop main exercises from the end. Warm-up and cool-down (first/last) always stay."""
    plan = [dict(item) for item in plan]
    budget = minutes * 60

    def estimate_seconds(items):
        return _estimate(items, pace=pace, transition=transition)

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


def convert_target(target: dict, kind: str) -> dict:
    """The same target expressed for another kind of exercise (reps ↔ hold/timed)."""
    target = {k: v for k, v in target.items() if k not in ("reps", "seconds")} | {
        k: v for k, v in target.items() if k in ("reps", "seconds")}
    if kind == "reps" and "reps" not in target:
        target.pop("seconds", None)
        target["reps"] = [8, 12]
    elif kind != "reps" and "seconds" not in target:
        target.pop("reps", None)
        target["seconds"] = DEFAULT_SECONDS[kind]
    elif kind == "reps":
        target.pop("seconds", None)
    else:
        target.pop("reps", None)
    return target


def _ms(moment: str | None) -> int | None:
    return int(datetime.fromisoformat(moment).timestamp() * 1000) if moment else None


class Training:
    def __init__(self, store: Store, library: Library | None = None):
        self.store = store
        self.library = library or Library()
        self.ladders = Ladders(store, self.library)

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
        target = {k: v for k, v in target.items() if k != "sides"}
        if spec.get("sides"):
            target["sides"] = spec["sides"]  # "each" | "alternating": reps and seconds count per side
        cursor = self.store.db.execute(
            "INSERT INTO session_exercises (session_id, exercise, name, pattern, kind, target) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, exercise_id, spec["name"], spec["pattern"], spec["kind"], json.dumps(target)),
        )
        return cursor.lastrowid

    # -- lifecycle -----------------------------------------------------------------------------

    def begin(self, *, now: datetime, kind: str, lock_key: str | None, minutes: float,
              equipment: frozenset[str] | set[str] = frozenset({"chair", "table", "bench", "doorway"}),
              generated: dict | None = None, mode: str | None = None) -> None:
        """`generated`: a fresh agent plan (hard + recovery); without one the local planner is used.
        `mode`: "hard" or "recovery" as decided for this lock; None lets the 48-hour rule decide."""
        if self.run is not None:
            return
        from . import recovery

        if generated:
            from .agent import choose

            if mode in ("hard", "recovery"):
                planned = dict(generated[mode])
            else:
                planned = dict(choose(generated, last_hard=self.ladders.last_hard_session(now), now=now))
            planned["note"] = generated.get("rationale", "")
            source = "generated"
        else:
            planned = self.ladders.plan(now, set(equipment), mode=mode)
            source = "local"
        plan = [{**item, "sides": self.library.get(item["exercise"]).get("sides")} for item in planned["plan"]]
        plan = fit_plan(plan, minutes, pace=recovery.pace_factor(self.store),
                        transition=recovery.transition_seconds(self.store))
        # finished_at is NOT NULL from the first schema; it is rewritten when the session closes.
        cursor = self.store.db.execute(
            "INSERT INTO sessions (day, started_at, finished_at, kind, lock_key, status, title, day_type, plan_source,"
            " notes) VALUES (?, ?, ?, ?, ?, 'in_progress', ?, ?, ?, '')",
            (now.date().isoformat(), _iso(now), _iso(now), kind, lock_key, planned["title"], planned["day_type"], source),
        )
        session_id = cursor.lastrowid
        order = [self._insert_exercise(session_id, item["exercise"], {k: v for k, v in item.items() if k != "exercise"})
                 for item in plan]
        self._save({"session_id": session_id, "order": order, "current": 0, "phase": "ready",
                    "note": planned.get("note", ""), "source": source,
                    "set_started_at": None, "set_ended_at": None, "last_set_end": None, "rest_until": None})

    def close(self, *, now: datetime, status: str) -> None:
        """End an unfinished session (abandoned when time ran out, overridden). When time ran out
        but all the main work was done (only the cool-down, or less, left), it counts as finished."""
        run = self.run
        if run is None:
            return
        notes = None
        if status == "abandoned" and self._main_work_done(run):
            status, notes = "finished", "Time ran out after the main work; counted as a session."
        self.store.db.execute("UPDATE sessions SET status = ?, finished_at = ?, notes = COALESCE(?, notes) WHERE id = ?",
                              (status, _iso(now), notes, run["session_id"]))
        self.store.delete(RUN_KEY)
        self._progress(run["session_id"], now)

    def _main_work_done(self, run: dict) -> bool:
        """Every exercise except the first (warm-up) and last (cool-down) is done or was swapped
        for an easier one that is done."""
        rows = [self._row(row_id) for row_id in run["order"]]
        main = rows[1:-1] if len(rows) > 2 else rows
        return bool(main) and all(r["status"] in ("done", "swapped") for r in main)

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
        self._progress(run["session_id"], now)
        return run["session_id"]

    def _progress(self, session_id: int, now: datetime) -> list[dict]:
        """Move the ladders from what was actually done (test sessions never count)."""
        kind = self.store.db.execute("SELECT kind FROM sessions WHERE id = ?", (session_id,)).fetchone()["kind"]
        return [] if kind == "test" else self.ladders.apply_session(session_id, now)

    # -- sets ----------------------------------------------------------------------------------

    def start_set(self, *, now: datetime, lead_in: int = 0) -> None:
        """Start a set after a `lead_in`-second get-ready countdown; the set is timed from its end."""
        run = self._require("ready", "resting")
        row = self._current(run)
        if row["started_at"] is None:
            self.store.db.execute("UPDATE session_exercises SET started_at = ? WHERE id = ?", (_iso(now), row["id"]))
        run.update(phase="running", set_started_at=_iso(now + timedelta(seconds=max(0, int(lead_in)))),
                   before_start={"phase": run["phase"], "rest_until": run["rest_until"]}, rest_until=None)
        self._save(run)

    def _in_lead_in(self, run: dict, now: datetime) -> bool:
        return run["phase"] == "running" and now < datetime.fromisoformat(run["set_started_at"])

    def go_now(self, *, now: datetime) -> None:
        """Skip the rest of the get-ready countdown."""
        run = self._require("running")
        if self._in_lead_in(run, now):
            run["set_started_at"] = _iso(now)
            self._save(run)

    def cancel_set(self, *, now: datetime) -> None:
        """Back out of a set during its get-ready countdown; nothing is logged."""
        run = self._require("running")
        if not self._in_lead_in(run, now):
            raise TrainingError("the set is already running; stop it instead")
        before = run.pop("before_start", None) or {"phase": "ready", "rest_until": None}
        run.update(phase=before["phase"], rest_until=before["rest_until"], set_started_at=None)
        self._save(run)

    def stop_set(self, *, now: datetime) -> None:
        run = self._require("running")
        if self._in_lead_in(run, now):
            raise TrainingError("the set hasn't started yet")
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
        seconds = (ended - started).total_seconds()
        if row["kind"] != "reps" and target.get("sides") == "each":
            seconds /= 2  # one timer runs both sides; holds are logged per side
        self.store.db.execute(
            "INSERT INTO sets (session_exercise_id, set_no, reps, seconds, load_kg, rest_seconds, started_at, ended_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (row["id"], len(done) + 1, None if row["kind"] != "reps" else int(reps), seconds,
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
        spec = self.library.get(row["exercise"])
        easier = spec.get("easier") if spec.get("chain") in LADDERED_CHAINS else None
        if not easier:
            raise TrainingError("no easier variation for this exercise")
        target = convert_target(json.loads(row["target"]), self.library.get(easier)["kind"])
        new_id = self._insert_exercise(run["session_id"], easier, target)
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

    def _times_done(self, exercise: str, session_id: int) -> int:
        return self.store.db.execute(
            "SELECT COUNT(*) FROM session_exercises WHERE exercise = ? AND status = 'done' AND session_id != ?",
            (exercise, session_id)).fetchone()[0]

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
                "has_easier": bool(spec.get("easier")) and spec.get("chain") in LADDERED_CHAINS,
                "status": row["status"], "rpe": row["rpe"], "sides": spec.get("sides") or "",
                "times_done": self._times_done(row["exercise"], run["session_id"]),
                "sets": [{"reps": s["reps"], "seconds": s["seconds"], "load_kg": s["load_kg"]} for s in self._sets(row_id)],
            })
        pending = None
        if run["phase"] == "logging":
            pending = (datetime.fromisoformat(run["set_ended_at"]) - datetime.fromisoformat(run["set_started_at"])).total_seconds()
        return {
            "id": run["session_id"], "title": session["title"], "day_type": session["day_type"], "kind": session["kind"],
            "note": run.get("note", ""), "source": run.get("source", "local"),
            "phase": run["phase"], "current": run["current"], "exercises": exercises,
            "set_started_at": _ms(run["set_started_at"]), "rest_until": _ms(run["rest_until"]),
            "pending_seconds": pending,
        }

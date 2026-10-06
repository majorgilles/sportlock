"""Progression ladders (one position per chain), applying rule proposals after a session, and the
local session planner that turns ladder positions into a plan.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from . import rules
from .library import Library
from .store import Store, _iso

# Where a beginner starts on each chain, with the first target.
START = {
    "push-horizontal": ("incline-push-up", {"sets": 3, "reps": [8, 12], "rest": 60}),
    "push-vertical": ("pike-push-up", {"sets": 3, "reps": [5, 8], "rest": 90}),
    "dip": ("bench-dip", {"sets": 3, "reps": [8, 12], "rest": 60}),
    "pull-horizontal": ("prone-y-raise", {"sets": 3, "reps": [10, 12], "rest": 45}),
    "pull-vertical": ("scapular-pull-up", {"sets": 3, "reps": [6, 10], "rest": 90}),
    "squat": ("bodyweight-squat", {"sets": 3, "reps": [12, 15], "rest": 60}),
    "hinge": ("glute-bridge", {"sets": 3, "reps": [12, 15], "rest": 45}),
    "core": ("plank", {"sets": 3, "seconds": 30, "rest": 45}),
    "core-flexion": ("crunch", {"sets": 3, "reps": [12, 15], "rest": 45}),
    "calf": ("calf-raise", {"sets": 3, "reps": [15, 20], "rest": 45}),
    "hip": ("standing-hip-abduction", {"sets": 2, "reps": [12, 15], "rest": 30}),
}
WARMUP = {"exercise": "dynamic-warmup", "sets": 1, "seconds": 300, "rest": 0}
COOLDOWN = {"exercise": "static-stretch", "sets": 1, "seconds": 300, "rest": 0}
HARD_DAY = ["push-horizontal", "squat", "pull-horizontal", "hinge", "core"]
MOBILITY_DAY = [
    {"exercise": "cat-cow", "sets": 2, "reps": [8, 10], "rest": 15},
    {"exercise": "worlds-greatest-stretch", "sets": 2, "reps": [4, 6], "rest": 15},
    {"exercise": "hip-flexor-stretch", "sets": 2, "seconds": 30, "rest": 15},
    {"exercise": "hamstring-stretch", "sets": 2, "seconds": 30, "rest": 15},
    {"exercise": "chest-doorway-stretch", "sets": 2, "seconds": 30, "rest": 15},
]
HARD_GAP = timedelta(hours=48)


class Ladders:
    def __init__(self, store: Store, library: Library):
        self.store = store
        self.library = library

    # -- positions -----------------------------------------------------------------------------

    def get(self, chain: str) -> dict:
        row = self.store.db.execute("SELECT * FROM ladders WHERE chain = ?", (chain,)).fetchone()
        if row:
            return {"chain": chain, "exercise": row["exercise"], "target": json.loads(row["target"]),
                    "reason": row["reason"]}
        exercise, target = START[chain]
        return {"chain": chain, "exercise": exercise, "target": dict(target), "reason": None}

    def all(self) -> list[dict]:
        return [self.get(chain) for chain in START]

    def _set(self, chain: str, exercise: str, target: dict, reason: str, now: datetime) -> None:
        self.store.db.execute(
            "INSERT OR REPLACE INTO ladders (chain, exercise, target, reason, updated_at) VALUES (?, ?, ?, ?, ?)",
            (chain, exercise, json.dumps(target), reason, _iso(now)),
        )

    # -- after a session -----------------------------------------------------------------------

    def apply_session(self, session_id: int, now: datetime) -> list[dict]:
        """Run the rules over every exercise done (or swapped) in the session, in order, and move
        the ladders. Returns the applied proposals."""
        applied = []
        rows = self.store.db.execute(
            "SELECT * FROM session_exercises WHERE session_id = ? AND status IN ('done', 'swapped') ORDER BY id",
            (session_id,),
        ).fetchall()
        for row in rows:
            spec = self.library.get(row["exercise"])
            chain = spec.get("chain")
            if chain not in START:
                continue  # warm-up, mobility and cool-down blocks are not laddered
            target = json.loads(row["target"])
            target.pop("progress", None)
            easier = self._neighbour(spec.get("easier"))
            harder = self._neighbour(spec.get("harder"))

            if row["status"] == "swapped":
                if not easier:
                    continue
                proposal = rules.too_hard(exercise=row["exercise"], name=row["name"], target=target, easier=easier)
            else:
                sets = [dict(s) for s in self.store.db.execute(
                    "SELECT reps, seconds FROM sets WHERE session_exercise_id = ? ORDER BY set_no", (row["id"],))]
                proposal = rules.propose(exercise=row["exercise"], name=row["name"], kind=row["kind"], target=target,
                                         sets=sets, rpe=row["rpe"], easier=easier, harder=harder)
            if proposal is None:
                continue

            self.store.db.execute(
                "INSERT INTO proposals (session_id, session_exercise_id, chain, rule, from_exercise, from_target,"
                " to_exercise, to_target, reason, status, decided_by, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'applied', 'rules', ?)",
                (session_id, row["id"], chain, proposal.rule, row["exercise"], json.dumps(target),
                 proposal.exercise, json.dumps(proposal.target), proposal.reason, _iso(now)),
            )
            self._set(chain, proposal.exercise, proposal.target, proposal.reason, now)
            applied.append({"chain": chain, "rule": proposal.rule, "exercise": proposal.exercise,
                            "target": proposal.target, "reason": proposal.reason})
        return applied

    def _neighbour(self, exercise_id: str | None) -> tuple[str, str] | None:
        return (exercise_id, self.library.get(exercise_id)["name"]) if exercise_id else None

    # -- planning ------------------------------------------------------------------------------

    def last_hard_session(self, now: datetime) -> datetime | None:
        row = self.store.db.execute(
            "SELECT MAX(finished_at) AS at FROM sessions WHERE status = 'finished' AND day_type = 'hard'"
            " AND kind NOT IN ('test', 'placeholder', 'outside')"
        ).fetchone()
        return datetime.fromisoformat(row["at"]) if row and row["at"] else None

    def plan(self, now: datetime, equipment: set[str], mode: str | None = None) -> dict:
        """Hard full-body day from the ladders, or a mobility day within 48 h of a hard one."""
        last_hard = self.last_hard_session(now)
        recovery_day = mode == "recovery" or (mode is None and last_hard and now - last_hard < HARD_GAP)
        if recovery_day:
            note = (f"Hard session {int((now - last_hard).total_seconds() // 3600)} h ago" if last_hard else "Recovery day")
            return {"title": "Mobility & recovery", "day_type": "mobility", "note": note,
                    "plan": [dict(WARMUP), *(i for i in (self._available(x, equipment) for x in MOBILITY_DAY) if i),
                             dict(COOLDOWN)]}

        items = [dict(WARMUP)]
        for chain in HARD_DAY:
            position = self.get(chain)
            exercise = self._fit_equipment(position["exercise"], equipment)
            if exercise is None:
                continue
            item = {"exercise": exercise, **position["target"]}
            if exercise != position["exercise"]:
                item["progress"] = f"{self.library.get(position['exercise'])['name']} needs equipment you don't have"
            elif position["reason"]:
                item["progress"] = position["reason"]
            items.append(item)
        items.append(dict(COOLDOWN))
        return {"title": "Full body", "day_type": "hard", "note": "", "plan": items}

    def _fit_equipment(self, exercise_id: str, equipment: set[str]) -> str | None:
        """The exercise itself, or the nearest easier one on its chain that needs no missing equipment."""
        current = exercise_id
        while current:
            spec = self.library.get(current)
            if set(spec.get("equipment", [])) <= equipment:
                return current
            current = spec.get("easier")
        return None

    def _available(self, item: dict, equipment: set[str]) -> dict | None:
        return dict(item) if set(self.library.get(item["exercise"]).get("equipment", [])) <= equipment else None

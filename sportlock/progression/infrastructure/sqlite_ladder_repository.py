"""SQLite adapter for ladders and the proposals that moved them."""

from __future__ import annotations

import json
from datetime import datetime
from typing import override

from sportlock.progression.domain.ladders import LadderMove, LadderPosition, LadderRepositoryProtocol
from sportlock.shared_kernel.infrastructure.database import Database
from sportlock.shared_kernel.targets import Target
from sportlock.shared_kernel.time import iso


class SqliteLadderRepository(LadderRepositoryProtocol):
    """ladders and proposals tables."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @override
    def saved(self) -> dict[str, LadderPosition]:
        return {row["chain"]: LadderPosition(chain=row["chain"], exercise=row["exercise"],
                                             target=Target.from_dict(json.loads(row["target"])), reason=row["reason"])
                for row in self.database.execute("SELECT * FROM ladders")}

    @override
    def set(self, position: LadderPosition, now: datetime) -> None:
        self.database.execute(
            "INSERT OR REPLACE INTO ladders (chain, exercise, target, reason, updated_at) VALUES (?, ?, ?, ?, ?)",
            (position.chain, position.exercise, json.dumps(position.target.to_dict()), position.reason, iso(now)))

    @override
    def reset(self, chain: str) -> None:
        self.database.execute("DELETE FROM ladders WHERE chain = ?", (chain,))

    @override
    def record_move(self, session_id: int, move: LadderMove, *, decided_by: str, now: datetime) -> None:
        self.database.execute(
            "INSERT INTO proposals (session_id, session_exercise_id, chain, rule, from_exercise, from_target,"
            " to_exercise, to_target, reason, status, decided_by, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'applied', ?, ?)",
            (session_id, move.session_exercise_id or None, move.chain, move.proposal.rule, move.from_exercise,
             json.dumps(move.from_target.to_dict()), move.proposal.exercise, json.dumps(move.proposal.target.to_dict()),
             move.proposal.reason, decided_by, iso(now)))

    @override
    def mark_last_overridden(self, chain: str, reason: str) -> None:
        self.database.execute(
            "UPDATE proposals SET status = 'overridden', override_reason = ? WHERE id = ("
            " SELECT MAX(id) FROM proposals WHERE chain = ? AND status = 'applied')", (reason, chain))

    @override
    def moves_for(self, session_id: int) -> list[dict]:
        return [{"chain": p["chain"], "rule": p["rule"], "from": p["from_exercise"], "to": p["to_exercise"],
                 "target": json.loads(p["to_target"]), "reason": p["reason"]}
                for p in self.database.execute("SELECT * FROM proposals WHERE session_id = ? ORDER BY id", (session_id,))]

"""SQLite adapters for ladders and the moves that changed them (the ladders and proposals tables)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import override

from sportlock.progression.domain.ladders import (
    LadderMove,
    LadderMoveRecord,
    LadderMoveRecordFilters,
    LadderMoveRecordRepositoryProtocol,
    LadderPosition,
    LadderPositionFilters,
    LadderPositionRepositoryProtocol,
)
from sportlock.progression.domain.rules import Proposal
from sportlock.shared_kernel.infrastructure.database import Database
from sportlock.shared_kernel.targets import Target
from sportlock.shared_kernel.time import iso


class SqliteLadderPositionRepository(LadderPositionRepositoryProtocol):
    """ladders table: one row per chain moved away from its start."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @override
    def find(self, filters: LadderPositionFilters) -> list[LadderPosition]:
        return [
            LadderPosition(
                chain=row["chain"],
                exercise=row["exercise"],
                target=Target.from_dict(json.loads(row["target"])),
                reason=row["reason"],
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            for row in self.database.execute("SELECT * FROM ladders")
        ]

    @override
    def update(self, entity: LadderPosition) -> LadderPosition:
        self.database.execute(
            "INSERT OR REPLACE INTO ladders (chain, exercise, target, reason, updated_at) VALUES (?, ?, ?, ?, ?)",
            (
                entity.chain,
                entity.exercise,
                json.dumps(entity.target.to_dict()),
                entity.reason,
                iso(entity.updated_at) if entity.updated_at else None,
            ),
        )
        return entity

    @override
    def delete(self, identifier: str) -> None:
        self.database.execute("DELETE FROM ladders WHERE chain = ?", (identifier,))


def _record(row: sqlite3.Row) -> LadderMoveRecord:
    return LadderMoveRecord(
        id=row["id"],
        session_id=row["session_id"],
        move=LadderMove(
            chain=row["chain"],
            session_exercise_id=row["session_exercise_id"] or 0,
            from_exercise=row["from_exercise"],
            from_target=Target.from_dict(json.loads(row["from_target"])),
            proposal=Proposal(
                rule=row["rule"],
                exercise=row["to_exercise"],
                target=Target.from_dict(json.loads(row["to_target"])),
                reason=row["reason"],
            ),
        ),
        decided_by=row["decided_by"],
        decided_at=datetime.fromisoformat(row["created_at"]),
        status=row["status"],
        override_reason=row["override_reason"],
    )


class SqliteLadderMoveRecordRepository(LadderMoveRecordRepositoryProtocol):
    """proposals table: one row per kept move."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @override
    def create(self, entity: LadderMoveRecord) -> LadderMoveRecord:
        move = entity.move
        identifier = self.database.execute(
            "INSERT INTO proposals (session_id, session_exercise_id, chain, rule, from_exercise, from_target,"
            " to_exercise, to_target, reason, status, decided_by, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entity.session_id,
                move.session_exercise_id or None,
                move.chain,
                move.proposal.rule,
                move.from_exercise,
                json.dumps(move.from_target.to_dict()),
                move.proposal.exercise,
                json.dumps(move.proposal.target.to_dict()),
                move.proposal.reason,
                entity.status,
                entity.decided_by,
                iso(entity.decided_at),
            ),
        ).lastrowid
        return entity.model_copy(update={"id": identifier})

    @override
    def find(self, filters: LadderMoveRecordFilters) -> list[LadderMoveRecord]:
        conditions, parameters = [], []
        for column, value in (("session_id", filters.session_id), ("chain", filters.chain), ("status", filters.status)):
            if value is not None:
                conditions.append(f"{column} = ?")
                parameters.append(value)
        where = " AND ".join(conditions) or "1"
        rows = self.database.execute(
            f"SELECT * FROM (SELECT * FROM proposals WHERE {where} ORDER BY id DESC LIMIT ?) ORDER BY id",
            (*parameters, -1 if filters.latest is None else filters.latest),
        )
        return [_record(row) for row in rows]

    @override
    def update(self, entity: LadderMoveRecord) -> LadderMoveRecord:
        self.database.execute(
            "UPDATE proposals SET status = ?, override_reason = ? WHERE id = ?",
            (entity.status, entity.override_reason, entity.id),
        )
        return entity

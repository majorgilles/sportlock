"""SQLite adapters for coaching: the plan (key-value), the run log, and the versioned memory."""

from __future__ import annotations

from datetime import date
from typing import override

from sportlock.coaching.domain.memory import CoachMemory, MemoryNote, NoteAdded, NoteDeleted, NoteForgotten, NoteUpdated
from sportlock.coaching.domain.plan import CoachPlan
from sportlock.coaching.domain.ports import (
    CoachMemoryRepositoryProtocol,
    CoachPlanRepositoryProtocol,
    CoachRun,
    CoachRunLogProtocol,
)
from sportlock.shared_kernel.infrastructure.database import Database

PLAN_KEY = "next_session"


class KvCoachPlanRepository(CoachPlanRepositoryProtocol):
    """The plan as one JSON value."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @override
    def get(self) -> CoachPlan | None:
        data = self.database.get(PLAN_KEY)
        return CoachPlan.from_dict(data) if data else None

    @override
    def save(self, plan: CoachPlan) -> None:
        self.database.put(PLAN_KEY, plan.to_dict())


class SqliteCoachRunLog(CoachRunLogProtocol):
    """coach_runs table."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @override
    def add(self, run: CoachRun) -> int:
        return self.database.execute("INSERT INTO coach_runs (at, seconds, ok, error) VALUES (?, ?, ?, ?)",
                                     (run.at, run.seconds, int(run.ok), run.error)).lastrowid

    @override
    def recent(self, limit: int) -> list[CoachRun]:
        rows = self.database.execute("SELECT * FROM (SELECT * FROM coach_runs ORDER BY id DESC LIMIT ?) ORDER BY id",
                                     (limit,)).fetchall()
        return [CoachRun(at=r["at"], seconds=r["seconds"], ok=bool(r["ok"]), error=r["error"]) for r in rows]


class SqliteCoachMemoryRepository(CoachMemoryRepositoryProtocol):
    """coach_memory_notes: one row per version of a note; the current version has no valid_to."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @override
    def load(self) -> CoachMemory:
        rows = self.database.execute("SELECT * FROM coach_memory_notes WHERE valid_to IS NULL").fetchall()
        notes = {r["note_id"]: MemoryNote(id=r["note_id"], topic=r["topic"], text=r["text"],
                                          since=date.fromisoformat(r["valid_from"])) for r in rows}
        top = self.database.execute("SELECT MAX(note_id) FROM coach_memory_notes").fetchone()[0]
        return CoachMemory(notes=notes, next_id=(top or 0) + 1)

    @override
    def save(self, memory: CoachMemory) -> None:
        for event in memory.pull_events():
            match event:
                case NoteAdded(note=note, by=by):
                    self._insert(note, by)
                case NoteUpdated(note=note, by=by):
                    self._end(note.id, note.since.isoformat(), by, "updated")
                    self._insert(note, by)
                case NoteDeleted(note_id=note_id, by=by, on=on):
                    self._end(note_id, on.isoformat(), by, "deleted")
                case NoteForgotten(note_id=note_id, on=on):
                    self._end(note_id, on.isoformat(), "user", "forgotten")

    def _insert(self, note: MemoryNote, by: str) -> None:
        self.database.execute("INSERT INTO coach_memory_notes (note_id, topic, text, valid_from, written_by)"
                              " VALUES (?, ?, ?, ?, ?)", (note.id, note.topic, note.text, note.since.isoformat(), by))

    def _end(self, note_id: int, day: str, by: str, reason: str) -> None:
        self.database.execute("UPDATE coach_memory_notes SET valid_to = ?, ended_by = ?, end_reason = ?"
                              " WHERE note_id = ? AND valid_to IS NULL", (day, by, reason, note_id))

    @override
    def forgotten(self, limit: int) -> list[str]:
        rows = self.database.execute("SELECT text FROM (SELECT id, text FROM coach_memory_notes WHERE end_reason = 'forgotten'"
                                     " ORDER BY id DESC LIMIT ?) ORDER BY id", (limit,))
        return [r["text"] for r in rows]

    @override
    def history(self, limit: int) -> list[dict]:
        rows = self.database.execute("SELECT note_id, topic, text, valid_from, valid_to, written_by, ended_by, end_reason"
                                     " FROM coach_memory_notes ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

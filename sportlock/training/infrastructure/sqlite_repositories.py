"""SQLite adapters for training: the session aggregate and read models over past sessions.

The active session's live position (phase, timers, order) is kept in the key-value table under
RUN_KEY, in the shape it has had since the first version, so a session survives restarts.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import override

from sportlock.shared_kernel.infrastructure.database import Database
from sportlock.shared_kernel.targets import Target
from sportlock.shared_kernel.time import iso
from sportlock.training.domain.counted_session import CountedSession, RecordedExercise, RecordedSet
from sportlock.training.domain.repositories import (
    CompletedExercise,
    CompletedExerciseFilters,
    CompletedExerciseRepositoryProtocol,
    CountedSessionFilters,
    CountedSessionRepositoryProtocol,
    ExerciseTiming,
    ExerciseTimingFilters,
    ExerciseTimingRepositoryProtocol,
    SessionSummary,
    SessionSummaryFilters,
    SessionSummaryRepositoryProtocol,
    TrainingSessionRepositoryProtocol,
    TransitionGap,
    TransitionGapFilters,
    TransitionGapRepositoryProtocol,
)
from sportlock.training.domain.session import LoggedSet, SessionExercise, TrainingSession

RUN_KEY = "training"
COUNTED = "kind NOT IN ('test', 'placeholder')"
CREDITED = "status = 'finished' AND kind NOT IN ('test', 'placeholder', 'outside')"


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _text(value: datetime | None) -> str | None:
    return iso(value) if value else None


class SqliteTrainingSessionRepository(TrainingSessionRepositoryProtocol):
    """sessions, session_exercises and sets tables, plus the live run in kv."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @override
    def get(self) -> TrainingSession | None:
        run = self.database.get(RUN_KEY)
        if not run:
            return None
        row = self.database.execute("SELECT * FROM sessions WHERE id = ?", (run["session_id"],)).fetchone()
        if row is None:
            return None
        exercises: dict[int, SessionExercise] = {}
        swaps: dict[int, int] = {}
        for exercise_id in run["order"]:
            e = self.database.execute("SELECT * FROM session_exercises WHERE id = ?", (exercise_id,)).fetchone()
            sets = [
                LoggedSet(
                    set_no=s["set_no"],
                    reps=s["reps"],
                    seconds=s["seconds"],
                    load_kg=s["load_kg"],
                    rest_seconds=s["rest_seconds"],
                    started_at=_dt(s["started_at"]),
                    ended_at=_dt(s["ended_at"]),
                    id=s["id"],
                )
                for s in self.database.execute(
                    "SELECT * FROM sets WHERE session_exercise_id = ? ORDER BY set_no", (exercise_id,)
                )
            ]
            exercises[exercise_id] = SessionExercise(
                exercise=e["exercise"],
                name=e["name"],
                pattern=e["pattern"],
                kind=e["kind"],
                target=Target.from_dict(json.loads(e["target"])),
                status=e["status"],
                rpe=e["rpe"],
                note=e["note"],
                skip_reason=e["skip_reason"],
                started_at=_dt(e["started_at"]),
                ended_at=_dt(e["ended_at"]),
                sets=sets,
                id=e["id"],
            )
            if e["swapped_to"]:
                swaps[exercise_id] = e["swapped_to"]
        for source, target in swaps.items():
            if target in exercises:
                exercises[source].swapped_to = exercises[target]
        before = run.get("before_start")
        if before and before.get("rest_until"):
            before = {**before, "rest_until": _dt(before["rest_until"])}
        return TrainingSession(
            id=row["id"],
            day=date.fromisoformat(row["day"]),
            started_at=_dt(row["started_at"]),
            kind=row["kind"],
            lock_key=row["lock_key"],
            title=row["title"] or "Session",
            day_type=row["day_type"] or "hard",
            plan_source=run.get("source", row["plan_source"] or "local"),
            coach_note=run.get("note", ""),
            status=row["status"],
            exercises=[exercises[i] for i in run["order"]],
            phase=run["phase"],
            current=run["current"],
            set_started_at=_dt(run.get("set_started_at")),
            set_ended_at=_dt(run.get("set_ended_at")),
            last_set_end=_dt(run.get("last_set_end")),
            rest_until=_dt(run.get("rest_until")),
            before_start=before,
        )

    @override
    def create(self, entity: TrainingSession) -> TrainingSession:
        session = entity
        # finished_at is NOT NULL since the first schema; it is rewritten when the session closes.
        cursor = self.database.execute(
            "INSERT INTO sessions (day, started_at, finished_at, kind, lock_key, status, title, day_type, plan_source,"
            " notes) VALUES (?, ?, ?, ?, ?, 'in_progress', ?, ?, ?, '')",
            (
                session.day.isoformat(),
                iso(session.started_at),
                iso(session.started_at),
                session.kind,
                session.lock_key,
                session.title,
                session.day_type,
                session.plan_source,
            ),
        )
        session.id = cursor.lastrowid
        return self.update(session)

    @override
    def update(self, entity: TrainingSession) -> TrainingSession:
        session = entity
        assert session.id is not None, "create the session first"
        for exercise in session.exercises:
            if exercise.id is None:
                exercise.id = self.database.execute(
                    "INSERT INTO session_exercises (session_id, exercise, name, pattern, kind, target)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        session.id,
                        exercise.exercise,
                        exercise.name,
                        exercise.pattern,
                        exercise.kind,
                        json.dumps(exercise.target.to_dict()),
                    ),
                ).lastrowid
        for exercise in session.exercises:
            self.database.execute(
                "UPDATE session_exercises SET exercise = ?, name = ?, pattern = ?, kind = ?, target = ?, status = ?,"
                " rpe = ?, note = ?, skip_reason = ?, swapped_to = ?, started_at = ?, ended_at = ? WHERE id = ?",
                (
                    exercise.exercise,
                    exercise.name,
                    exercise.pattern,
                    exercise.kind,
                    json.dumps(exercise.target.to_dict()),
                    exercise.status,
                    exercise.rpe,
                    exercise.note,
                    exercise.skip_reason,
                    exercise.swapped_to.id if exercise.swapped_to else None,
                    _text(exercise.started_at),
                    _text(exercise.ended_at),
                    exercise.id,
                ),
            )
            for logged in exercise.sets:
                if logged.id is None:
                    logged.id = self.database.execute(
                        "INSERT INTO sets (session_exercise_id, set_no, reps, seconds, load_kg, rest_seconds,"
                        " started_at, ended_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            exercise.id,
                            logged.set_no,
                            logged.reps,
                            logged.seconds,
                            logged.load_kg,
                            logged.rest_seconds,
                            iso(logged.started_at),
                            iso(logged.ended_at),
                        ),
                    ).lastrowid
        self.database.execute(
            "UPDATE sessions SET status = ?, finished_at = ?, rpe = ?, notes = COALESCE(NULLIF(?, ''), notes),"
            " calories = ?, avg_hr = ?, body_weight = ? WHERE id = ?",
            (
                session.status,
                iso(session.finished_at or session.started_at),
                session.rpe,
                session.notes,
                session.calories,
                session.avg_hr,
                session.body_weight,
                session.id,
            ),
        )
        if session.active:
            before = session.before_start
            if before:
                before = {**before, "rest_until": _text(before.get("rest_until"))}
            self.database.put(
                RUN_KEY,
                {
                    "session_id": session.id,
                    "order": [e.id for e in session.exercises],
                    "current": session.current,
                    "phase": session.phase,
                    "note": session.coach_note,
                    "source": session.plan_source,
                    "set_started_at": _text(session.set_started_at),
                    "set_ended_at": _text(session.set_ended_at),
                    "last_set_end": _text(session.last_set_end),
                    "rest_until": _text(session.rest_until),
                    "before_start": before,
                },
            )
        else:
            self.database.delete(RUN_KEY)
        return session


class SqliteSessionSummaryRepository(SessionSummaryRepositoryProtocol):
    """Credited sessions from the sessions table, with the average exercise effort when the session has none."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @override
    def find(self, filters: SessionSummaryFilters) -> list[SessionSummary]:
        conditions, parameters = [CREDITED], []
        if filters.finished_since is not None:
            conditions.append("finished_at >= ?")
            parameters.append(iso(filters.finished_since))
        if filters.day_type is not None:
            conditions.append("day_type = ?")
            parameters.append(filters.day_type)
        rows = self.database.execute(
            "SELECT id, day, started_at, finished_at, day_type, rpe, title,"
            " (SELECT AVG(e.rpe) FROM session_exercises e WHERE e.session_id = sessions.id AND e.rpe IS NOT NULL)"
            f" AS exercise_rpe FROM sessions WHERE {' AND '.join(conditions)} ORDER BY finished_at",
            parameters,
        )
        result = []
        for row in rows:
            started, finished = _dt(row["started_at"]), datetime.fromisoformat(row["finished_at"])
            rpe = row["rpe"]
            if rpe is None:  # ran out of time before the summary: average the exercises
                rpe = round(row["exercise_rpe"]) if row["exercise_rpe"] else None
            result.append(
                SessionSummary(
                    id=row["id"],
                    day=date.fromisoformat(row["day"]),
                    finished_at=finished,
                    day_type=row["day_type"],
                    title=row["title"],
                    minutes=round((finished - started).total_seconds() / 60) if started else 0,
                    rpe=rpe,
                )
            )
        return result


class SqliteExerciseTimingRepository(ExerciseTimingRepositoryProtocol):
    """Done exercises with their start and end, from the session_exercises table."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @override
    def find(self, filters: ExerciseTimingFilters) -> list[ExerciseTiming]:
        rows = self.database.execute(
            "SELECT e.target, e.started_at, e.ended_at,"
            " (SELECT COUNT(*) FROM sets s WHERE s.session_exercise_id = e.id) AS n"
            " FROM session_exercises e JOIN sessions x ON x.id = e.session_id"
            " WHERE e.status = 'done' AND e.started_at IS NOT NULL AND e.ended_at IS NOT NULL"
            " AND x.kind NOT IN ('test', 'placeholder')"
            " AND e.session_id IN (SELECT id FROM sessions ORDER BY id DESC LIMIT ?)",
            (filters.last_sessions,),
        ).fetchall()
        return [
            ExerciseTiming(
                target=Target.from_dict(json.loads(r["target"])),
                sets_done=r["n"],
                started_at=datetime.fromisoformat(r["started_at"]),
                ended_at=datetime.fromisoformat(r["ended_at"]),
            )
            for r in rows
        ]


class SqliteTransitionGapRepository(TransitionGapRepositoryProtocol):
    """Gaps from a session's start to its first exercise, and between its exercises."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @override
    def find(self, filters: TransitionGapFilters) -> list[TransitionGap]:
        gaps = []
        for session in self.database.execute(
            f"SELECT id, started_at FROM sessions WHERE {COUNTED} AND started_at IS NOT NULL ORDER BY id DESC LIMIT ?",
            (filters.last_sessions,),
        ).fetchall():
            previous_end = session["started_at"]
            for row in self.database.execute(
                "SELECT started_at, ended_at FROM session_exercises WHERE session_id = ?"
                " AND started_at IS NOT NULL ORDER BY started_at",
                (session["id"],),
            ).fetchall():
                if previous_end:
                    seconds = (
                        datetime.fromisoformat(row["started_at"]) - datetime.fromisoformat(previous_end)
                    ).total_seconds()
                    gaps.append(TransitionGap(seconds=seconds))
                previous_end = row["ended_at"]
        return gaps


class SqliteCompletedExerciseRepository(CompletedExerciseRepositoryProtocol):
    """Done exercises from the session_exercises table."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @override
    def find(self, filters: CompletedExerciseFilters) -> list[CompletedExercise]:
        conditions, parameters = ["status = 'done'"], []
        if filters.exercise is not None:
            conditions.append("exercise = ?")
            parameters.append(filters.exercise)
        if filters.excluding_session is not None:
            conditions.append("session_id != ?")
            parameters.append(filters.excluding_session)
        rows = self.database.execute(
            f"SELECT session_id, exercise FROM session_exercises WHERE {' AND '.join(conditions)} ORDER BY id",
            parameters,
        )
        return [CompletedExercise(session_id=row["session_id"], exercise=row["exercise"]) for row in rows]


class SqliteCountedSessionRepository(CountedSessionRepositoryProtocol):
    """Counted, closed sessions with their exercises and sets."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def _exercises(self, session_id: int) -> tuple[RecordedExercise, ...]:
        exercises = []
        for row in self.database.execute(
            "SELECT * FROM session_exercises WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall():
            sets = tuple(
                RecordedSet(
                    reps=set_row["reps"],
                    seconds=set_row["seconds"],
                    load_kg=set_row["load_kg"],
                    rest_seconds=set_row["rest_seconds"],
                )
                for set_row in self.database.execute(
                    "SELECT * FROM sets WHERE session_exercise_id = ? ORDER BY set_no", (row["id"],)
                )
            )
            exercises.append(
                RecordedExercise(
                    exercise=row["exercise"],
                    name=row["name"],
                    kind=row["kind"],
                    status=row["status"],
                    target=json.loads(row["target"]),
                    sets=sets,
                    rpe=row["rpe"],
                    note=row["note"],
                    skip_reason=row["skip_reason"],
                )
            )
        return tuple(exercises)

    @override
    def find(self, filters: CountedSessionFilters) -> list[CountedSession]:
        conditions, parameters = [COUNTED, "status != 'in_progress'"], []
        if filters.first_day is not None:
            conditions.append("day >= ?")
            parameters.append(filters.first_day.isoformat())
        if filters.last_day is not None:
            conditions.append("day <= ?")
            parameters.append(filters.last_day.isoformat())
        order = "id DESC" if filters.newest_first else "started_at"
        limit = -1 if filters.latest is None else filters.latest
        rows = self.database.execute(
            f"SELECT * FROM sessions WHERE {' AND '.join(conditions)} ORDER BY {order} LIMIT ?",
            (*parameters, limit),
        ).fetchall()
        return [
            CountedSession(
                id=row["id"],
                day=row["day"],
                started_at=row["started_at"],
                finished_at=row["finished_at"],
                kind=row["kind"],
                status=row["status"],
                title=row["title"],
                day_type=row["day_type"],
                plan_source=row["plan_source"],
                rpe=row["rpe"],
                notes=row["notes"],
                exercises=self._exercises(row["id"]),
            )
            for row in rows
        ]

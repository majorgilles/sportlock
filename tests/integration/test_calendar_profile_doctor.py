import json
import sqlite3
from datetime import timedelta

from sportlock.progression.domain.ladders import positions
from sportlock.shared_kernel.infrastructure.database import Database
from tests.world import GOOD_PLAN, World


def test_calendar__history_and_plans__weeks_with_the_next_lock_in_detail(world):
    world.add_session(world.clock.time - timedelta(hours=20))
    calendar = world.cmd(cmd="calendar")["calendar"]
    days = {d["date"]: d for d in calendar["days"]}
    assert len(calendar["days"]) % 7 == 0
    yesterday = (world.clock.time - timedelta(hours=20)).date().isoformat()
    assert days[yesterday]["sessions"][0]["status"] == "finished"
    today = days[world.clock.time.date().isoformat()]
    assert today["planned"][0]["mode"] == "recovery" and today["planned"][0]["next"]
    later = [p for d in calendar["days"] for p in d.get("planned", []) if not p.get("next")]
    assert all(p["mode"] == "later" for p in later)


def test_calendar__coach_feedback__under_that_session_and_the_next_lock(world):
    world.add_session(world.clock.time - timedelta(hours=50))
    tips = [{"about": "Push-ups too easy", "advice": "Lower the hand height."}]
    world.coach.output = {**GOOD_PLAN, "recommendations": tips}
    world.c.run_coach.execute(world.daemon.coach_command(world.clock.time))
    days = {d["date"]: d for d in world.cmd(cmd="calendar")["calendar"]["days"]}
    session_day = (world.clock.time - timedelta(hours=50)).date().isoformat()
    assert days[session_day]["sessions"][0]["recommendations"] == tips
    assert days[world.clock.time.date().isoformat()]["planned"][0]["recommendations"] == tips


def test_profile_save__first_time_intermediate__ladders_start_higher_but_later_edits_keep_progress(tmp_path, catalogue):
    world = World(tmp_path, catalogue, profile=False)
    assert world.cmd(cmd="profile-save", profile={"experience": "intermediate", "goals": ["muscle"]})["ok"]
    assert {p.chain: p.exercise for p in positions(world.c.ladders.saved())}["push-horizontal"] == "push-up"
    world.c.database.execute("UPDATE ladders SET exercise = 'diamond-push-up' WHERE chain = 'push-horizontal'")
    world.cmd(cmd="profile-save", profile={"experience": "advanced", "goals": ["muscle"]})
    assert {p.chain: p.exercise for p in positions(world.c.ladders.saved())}["push-horizontal"] == "diamond-push-up"


def test_profile_save__invalid__refused(world):
    assert not world.cmd(cmd="profile-save", profile={"experience": "expert", "goals": []})["ok"]


def test_doctor__clean_data__no_errors(world):
    assert [i for i in world.cmd(cmd="doctor")["issues"] if i["severity"] == "error"] == []


def test_doctor__timed_exercise_with_a_rep_target__found_and_repaired(world):
    world.c.database.execute(
        "INSERT INTO sessions (id, day, finished_at, kind, status) VALUES (1, '2026-10-05', 'x', 'test', 'abandoned')"
    )
    world.c.database.execute(
        "INSERT INTO session_exercises (session_id, exercise, name, pattern, kind, target, status)"
        " VALUES (1, 'static-stretch', 'Cool-down stretching', 'mobility', 'timed', ?, 'done')",
        (json.dumps({"sets": 2, "rest": 15, "reps": [8, 10]}),),
    )
    assert world.cmd(cmd="doctor", fix=True)["fixed"] == 1
    target = json.loads(world.c.database.execute("SELECT target FROM session_exercises").fetchone()[0])
    assert target == {"sets": 2, "rest": 15, "seconds": 120}


def test_doctor__stuck_session_and_leftover_run__repaired_outside_a_lock(world):
    world.c.database.execute(
        "INSERT INTO sessions (id, day, finished_at, kind, status) VALUES (2, '2026-10-05', 'x', 'scheduled', 'in_progress')"
    )
    world.c.database.put("training", {"session_id": 99})
    world.cmd(cmd="doctor", fix=True)
    assert world.c.database.execute("SELECT status FROM sessions WHERE id = 2").fetchone()[0] == "abandoned"
    assert world.c.database.get("training") is None


def test_doctor__ladder_on_the_wrong_chain__reset(world):
    world.c.database.execute("INSERT INTO ladders VALUES ('squat', 'plank', '{}', NULL, 'x')")
    world.cmd(cmd="doctor", fix=True)
    assert world.c.database.execute("SELECT COUNT(*) FROM ladders").fetchone()[0] == 0


def test_report__written_with_the_note_and_the_doctor(world):
    path = world.cmd(cmd="report", note="lock screen froze")["path"]
    text = open(path).read()
    assert "lock screen froze" in text and "## Doctor" in text


def test_database__version_two_database__coach_runs_moved_out_of_the_key_value_table(tmp_path):
    # given: a database from before the coach_runs table
    path = tmp_path / "old.db"
    Database(path).db.close()
    db = sqlite3.connect(path)
    db.executescript("DROP TABLE coach_runs; DROP TABLE coach_memory_notes; PRAGMA user_version = 2;")
    db.execute(
        "INSERT INTO kv VALUES ('agent_runs', ?)",
        (json.dumps([{"at": "2026-10-01T10:00:00", "seconds": 60, "ok": True, "error": None}]),),
    )
    db.commit()
    db.close()
    # when
    database = Database(path)
    # then
    assert [tuple(r) for r in database.execute("SELECT at, ok FROM coach_runs")] == [("2026-10-01T10:00:00", 1)]
    assert database.get("agent_runs") is None

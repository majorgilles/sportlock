from datetime import timedelta

from sportlock.progression.domain.ladders import positions
from sportlock.training.application.session_services import BeginTrainingSessionCommand

HOUSE = frozenset({"chair", "table", "bench", "doorway"})


def _ladder(world, chain):
    return {p.chain: p for p in positions(world.c.ladders.saved())}[chain]


def _do_set(world, seconds, reps=None):
    world.clock.time += timedelta(seconds=10)
    assert world.train("start_set")["ok"]
    world.clock.time += timedelta(seconds=5 + seconds)  # after the get-ready countdown
    assert world.train("stop_set")["ok"]
    assert world.train("save_set", **({"reps": reps} if reps is not None else {}))["ok"]


def _session_with_push(world, push_reps, push_rpe):
    """Warm-up, then every planned set of the push exercise at `push_reps`, skip the rest, finish."""
    world.at("18:00")
    _do_set(world, 300)
    assert world.train("rate", rpe=3)["ok"]
    for _ in range(world.c.sessions.active().exercises[1].target.sets):
        _do_set(world, 30, push_reps)
    assert world.train("rate", rpe=push_rpe)["ok"]
    return world.finish_session()


def test_finish__easy_push_ups__ladder_moves_up_and_the_next_plan_uses_it(world):
    # when
    assert _session_with_push(world, 12, 5)["ok"]
    # then
    position = _ladder(world, "push-horizontal")
    assert position.exercise == "knee-push-up" and "Knee push-up" in position.reason
    world.clock.time += timedelta(days=3)
    session = world.c.begin_session.execute(BeginTrainingSessionCommand(
        kind="manual", lock_key=None, minutes=60, equipment=HOUSE), world.clock.time)
    [push] = [e for e in session.exercises if e.exercise == "knee-push-up"]
    assert "top of the range" in push.target.progress


def test_swap_easier__during_a_session__ladder_moves_down_as_too_hard(world):
    world.at("18:00")
    assert world.train("skip", reason="testing")["ok"]
    assert world.train("swap_easier")["ok"]
    assert world.finish_session()["ok"]
    assert _ladder(world, "push-horizontal").exercise == "wall-push-up"
    assert world.c.database.execute("SELECT rule FROM proposals").fetchone()[0] == "too-hard"


def test_test_session__finished__ladders_untouched(world):
    world.at("12:00")
    world.cmd(cmd="test")
    world.at("12:00")
    _do_set(world, 20)
    assert world.train("rate", rpe=3)["ok"]
    assert world.finish_session()["ok"]
    assert _ladder(world, "push-horizontal").exercise == "incline-push-up"


def test_close__time_runs_out_in_the_cool_down__counts_as_a_session(world):
    world.at("18:00")
    session = world.c.sessions.active()
    for exercise in session.exercises[:-1]:
        for _ in range(exercise.target.sets):
            _do_set(world, 20, 10 if exercise.kind == "reps" else None)
        assert world.train("rate", rpe=6)["ok"]
    world.at("18:30")
    status, notes = world.c.database.execute("SELECT status, notes FROM sessions").fetchone()
    assert status == "finished" and "main work" in notes
    assert world.clock.time.date() in world.c.history.trained_days()


def test_close__main_work_skipped__does_not_count(world):
    world.at("18:00")
    assert world.train("skip", reason="shoulder")["ok"]
    world.at("18:30")
    assert world.c.database.execute("SELECT status FROM sessions").fetchone()[0] == "abandoned"


def test_snapshot__running_set__phase_timer_and_exercise_details(world):
    world.at("18:00")
    world.train("start_set")
    training = world.at("18:00", second=1)["training"]
    assert training["phase"] == "running"
    assert training["set_started_at"] == int((world.clock.time.replace(second=5)).timestamp() * 1000)
    first = training["exercises"][0]
    assert first["name"] == "Dynamic warm-up" and first["times_done"] == 0 and not first["has_easier"]


def test_snapshot__exercises_on_both_sides__sides_shown(world):
    world.at("18:00")
    session = world.c.sessions.active()
    session.exercises[1].target = session.exercises[1].target.with_(sides="each")
    world.c.sessions.save(session)
    exercises = world.at("18:01")["training"]["exercises"]
    assert exercises[1]["target"]["sides"] == "each"


def test_session__survives_a_restart_of_the_service(world, tmp_path, catalogue):
    from tests.world import World

    world.at("18:00")
    _do_set(world, 300)
    assert world.train("rate", rpe=3)["ok"]
    world.train("start_set")
    # when: a new process on the same database
    restarted = World.__new__(World)
    restarted.__dict__.update(world.__dict__)
    from sportlock.app.container import Container
    from sportlock.app.daemon import Daemon
    restarted.c = Container(db_path=tmp_path / "db.sqlite", config_path=world.config_path, library_dir=tmp_path / "library",
                            desktop=world.desktop, lock_screen=world.lock_screen, popup=world.popup, coach=world.coach,
                            clock=world.clock, catalogue=catalogue)
    restarted.daemon = Daemon(restarted.c, state_path=tmp_path / "state.json")
    restarted.daemon.running = False
    restarted.daemon.coach_enabled = False
    # then
    state = restarted.at("18:02")
    assert state["locked"] and state["training"]["current"] == 1 and state["training"]["phase"] == "running"

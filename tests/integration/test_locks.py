from datetime import timedelta

from tests.world import CONFIG, GOOD_PLAN, World


def test_tick__scheduled_lock__warns_locks_then_expires_and_restores(world):
    # when
    assert not world.at("17:00")["locked"]
    world.at("17:50")
    # then: warned once, with a popup
    assert world.desktop.notifications == ["Training lock at 18:00"]
    assert [p["headline"] for p in world.popup.shown] == ["Training lock at 18:00"]
    world.at("17:51")
    assert len(world.desktop.notifications) == 1
    world.at("17:58")
    assert len(world.desktop.notifications) == 2 and len(world.popup.shown) == 1  # popup with the first warning only

    state = world.at("18:00")
    assert state["locked"] and state["training"]["phase"] == "ready"
    assert world.desktop.playing == [] and world.desktop.stay_awake
    assert world.lock_screen.shown > 0 and world.popup.closed > 0

    assert not world.at("18:30")["locked"]
    assert world.desktop.playing == ["org.mpris.MediaPlayer2.spotify"] and not world.desktop.stay_awake
    assert world.c.lock_events.recent(1)[0]["outcome"] == "expired"
    assert world.c.database.execute("SELECT status FROM sessions").fetchone()[0] == "abandoned"


def test_train__actions_over_the_socket__follow_the_session_phases(world):
    world.at("18:00")
    assert world.train("start_set")["ok"]
    assert not world.train("rate", rpe=5)["ok"]  # wrong phase
    assert not world.train("nope")["ok"]
    assert not world.train("stop_set")["ok"]  # still in the 5 s get-ready countdown
    world.clock.time = world.clock.time.replace(second=6)
    assert world.train("stop_set")["ok"]
    assert world.at("18:05")["training"]["phase"] == "logging"


def test_finish__a_session__unlocks_counts_and_says_what_changes(world):
    world.at("18:05")
    assert world.finish_session()["ok"]
    assert not world.at("18:06")["locked"]
    assert world.daemon.state["trained_today"]
    assert world.c.lock_events.recent(1)[0]["outcome"] == "completed"
    assert "Session done ✓" in world.desktop.notifications


def test_override__needs_the_phrase_then_waits_out_the_countdown(world):
    world.at("18:00")
    assert not world.cmd(cmd="override", phrase="let me out")["ok"]
    assert world.cmd(cmd="override", phrase="i am choosing  to skip my training TODAY")["ok"]
    assert world.at("18:04")["locked"]
    assert not world.at("18:05")["locked"]
    assert world.c.lock_events.recent(1)[0]["outcome"] == "override"
    assert not world.at("18:10")["locked"]  # stays ended
    assert world.c.database.execute("SELECT status FROM sessions").fetchone()[0] == "overridden"


def test_cancel_override__the_lock_stays(world):
    world.at("18:00")
    world.cmd(cmd="override", phrase="I am choosing to skip my training today")
    world.cmd(cmd="cancel-override")
    assert world.at("18:06")["locked"]


def test_tick__system_lock_screen_up__waits_for_it(world):
    world.desktop.system_locked = True
    state = world.at("18:00")
    assert not state["locked"] and state["waiting_for_omarchy_lock"]
    world.desktop.system_locked = False
    assert world.at("18:20")["locked"]


def test_tick__nothing_was_playing__nothing_resumed(world):
    world.desktop.playing, world.desktop.stay_awake = [], True
    world.at("18:00")
    world.at("18:30")
    assert world.desktop.playing == [] and world.desktop.stay_awake


def test_settings__changed_during_a_lock__applied_after_it(world):
    world.at("18:00")
    world.config_path.write_text(CONFIG.replace("minutes = 30", "minutes = 5"))
    state = world.at("18:10")
    assert state["locked"] and state["config_pending"]
    world.at("18:30")
    assert not world.c.settings.pending and world.c.settings.settings.locks[0].minutes == 5


def test_test_lock__one_minute__not_overridable_and_never_counts(world):
    world.at("12:00")
    world.cmd(cmd="test")
    assert world.at("12:00")["locked"]
    assert not world.cmd(cmd="override", phrase="I am choosing to skip my training today")["ok"]
    assert world.finish_session()["ok"]
    assert not world.at("12:00", second=1)["locked"]
    assert world.at("18:00")["locked"]  # a test session is not training


def test_test_lock__runs_out_after_a_minute(world):
    world.at("12:00")
    world.cmd(cmd="test")
    assert world.at("12:00")["locked"]
    assert not world.at("12:01", second=1)["locked"]


def test_start__manual_session__locks_and_counts_for_the_day(world):
    world.at("12:00")
    assert not world.cmd(cmd="start", minutes=5)["ok"]
    assert world.cmd(cmd="start", minutes=20)["ok"]
    assert world.at("12:01")["lock"]["kind"] == "manual"
    assert world.finish_session()["ok"]
    assert not world.at("12:02")["locked"]
    assert not world.at("18:00")["locked"]  # trained today: the scheduled lock is skipped


def test_tick__no_profile_yet__scheduled_locks_wait(tmp_path, catalogue):
    world = World(tmp_path, catalogue, profile=False)
    assert not world.at("18:00")["locked"]
    assert not world.daemon.state["setup"]["profile"]
    world.cmd(cmd="profile-save", profile={"experience": "beginner", "goals": ["strength"]})
    assert world.at("18:05")["locked"]


def test_settings_save__from_the_app__applied_or_waiting_for_the_lock(world):
    settings = world.cmd(cmd="settings-get")["settings"]
    settings["locks"].append({"days": ["mon"], "at": "07:00", "minutes": 15})
    world.at("12:00")
    assert world.cmd(cmd="settings-save", settings=settings) == {"ok": True, "pending": False}
    assert len(world.c.settings.settings.locks) == 2

    world.at("18:05")  # locked: a change now waits
    settings["locks"] = settings["locks"][:1]
    assert world.cmd(cmd="settings-save", settings=settings)["pending"]
    assert len(world.c.settings.settings.locks) == 2
    world.at("18:30")
    assert len(world.c.settings.settings.locks) == 1


def test_tick__hard_session_yesterday__shorter_recovery_lock_that_does_not_relock(world):
    world.add_session(world.clock.time - timedelta(hours=20))
    state = world.at("18:00")
    assert state["locked"] and state["training"]["day_type"] == "mobility"
    assert (state["lock"]["end"] - state["lock"]["start"]) // 60000 == 15
    assert not world.at("18:16")["locked"]
    assert not world.at("18:20")["locked"]  # the scheduled window runs to 18:30: no relock


def test_tick__coach_advises_rest_and_guardrails_allow__no_lock(world):
    # given: three sessions this week, the last one yesterday, and a fresh plan advising rest
    for hours in (20, 70, 120):
        world.add_session(world.clock.time - timedelta(hours=hours))
    world.coach.output = {**GOAL_REST}
    world.c.run_coach.execute(world.daemon.coach_command(world.clock.time))
    # when
    world.at("17:50")  # decided 10 minutes before
    # then
    assert "Rest day: no lock at 18:00" in world.desktop.notifications
    assert not world.at("18:00")["locked"] and not world.at("18:10")["locked"]
    assert world.c.lock_events.recent(1)[0]["outcome"] == "rest"
    assert "Training lock at 18:00" not in world.desktop.notifications


GOAL_REST = {**GOOD_PLAN, "next_lock": {"mode": "rest", "recovery_minutes": None, "reason": "Yesterday's 45 min at effort 8"}}


def test_tick__popup_off_in_settings__notification_only(tmp_path, catalogue):
    world = World(tmp_path, catalogue, config=CONFIG.replace("max_minutes_per_day = 60",
                                                             "max_minutes_per_day = 60\nwarn_popup = false"))
    world.at("17:50")
    assert world.desktop.notifications == ["Training lock at 18:00"]
    assert world.popup.shown == []


def test_tick__warning_popup__shows_the_coach_tips(world):
    world.coach.output = {**GOOD_PLAN, "recommendations": [{"about": "Push-ups too easy", "advice": "Use a desk."}]}
    world.c.run_coach.execute(world.daemon.coach_command(world.clock.time))
    world.at("17:50")
    [popup] = world.popup.shown
    assert popup["recommendations"] == [{"about": "Push-ups too easy", "advice": "Use a desk."}]
    assert popup["title"] == "Push focus"

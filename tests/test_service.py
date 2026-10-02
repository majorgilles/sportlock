import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from sportlock import service as service_mod
from sportlock.store import Store

CONFIG = """
[general]
enabled = true
max_minutes_per_day = 60
[override]
phrase = "I am choosing to skip my training today"
wait_seconds = 300
[[lock]]
days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
at = "18:00"
minutes = 30
"""


class FakeDesktop:
    def __init__(self):
        self.playing = ["org.mpris.MediaPlayer2.spotify"]
        self.paused = []
        self.stay_awake = False
        self.omarchy_locked = False
        self.notifications = []

    def pause_media(self):
        self.paused, self.playing = self.playing, []
        return list(self.paused)

    def resume_media(self, names):
        self.playing += names
        self.paused = []

    def patches(self):
        s = "sportlock.service.system."
        return [
            mock.patch(s + "pause_media", self.pause_media),
            mock.patch(s + "resume_media", self.resume_media),
            mock.patch(s + "idle_stay_awake", lambda: self.stay_awake),
            mock.patch(s + "set_idle_stay_awake", lambda v: setattr(self, "stay_awake", v)),
            mock.patch(s + "omarchy_lock_active", lambda: self.omarchy_locked),
            mock.patch(s + "notify", lambda *a, **k: self.notifications.append(a[0])),
            mock.patch(s + "theme", lambda: {}),
        ]


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "config.toml").write_text(CONFIG)
        self.desktop = FakeDesktop()
        self.now = datetime(2026, 10, 5, 17, 0)
        patches = self.desktop.patches() + [
            mock.patch.object(service_mod, "RUNTIME_DIR", self.tmp),
            mock.patch.object(service_mod, "STATE_PATH", self.tmp / "state.json"),
            mock.patch.object(service_mod, "now_local", lambda: self.now),
            mock.patch.object(service_mod.Service, "_ensure_locker", lambda _: None),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.svc = service_mod.Service(store=Store(self.tmp / "db.sqlite"), config_path=self.tmp / "config.toml")
        self.svc.running = False  # no background ticks
        self.svc.agent_enabled = False  # never call Claude from tests
        self.svc.command({"cmd": "profile-save", "profile": {"experience": "beginner", "goals": ["general fitness"],
                                                             "equipment": ["chair", "table", "bench", "doorway"]}})

    def at(self, hhmm):
        hours, minutes = map(int, hhmm.split(":"))
        self.now = self.now.replace(hour=hours, minute=minutes, second=0)
        self.svc.tick()
        return self.svc.state

    def test_full_cycle_expires_and_restores(self):
        self.assertFalse(self.at("17:00")["locked"])
        self.at("17:50")
        self.assertEqual(self.desktop.notifications, ["Training lock at 18:00"])
        self.at("17:51")
        self.assertEqual(len(self.desktop.notifications), 1)  # not repeated

        state = self.at("18:00")
        self.assertTrue(state["locked"])
        self.assertEqual(state["training"]["phase"], "ready")
        self.assertEqual(self.desktop.playing, [])
        self.assertTrue(self.desktop.stay_awake)

        self.assertFalse(self.at("18:30")["locked"])
        self.assertEqual(self.desktop.playing, ["org.mpris.MediaPlayer2.spotify"])
        self.assertFalse(self.desktop.stay_awake)
        self.assertEqual(self.svc.store.recent_locks()[0]["outcome"], "expired")
        status = self.svc.store.db.execute("SELECT status FROM sessions").fetchone()[0]
        self.assertEqual(status, "abandoned")

    def finish_session(self):
        while self.svc.training.run["phase"] != "summary":
            self.assertTrue(self.svc.command({"cmd": "train", "action": "skip", "reason": "testing"})["ok"])
        return self.svc.command({"cmd": "train", "action": "finish", "rpe": 5})

    def test_training_actions_over_commands(self):
        self.at("18:00")
        cmd = self.svc.command
        self.assertTrue(cmd({"cmd": "train", "action": "start_set"})["ok"])
        self.assertFalse(cmd({"cmd": "train", "action": "rate", "rpe": 5})["ok"])  # wrong phase
        self.assertFalse(cmd({"cmd": "train", "action": "nope"})["ok"])
        self.assertTrue(cmd({"cmd": "train", "action": "stop_set"})["ok"])
        self.assertEqual(self.at("18:05")["training"]["phase"], "logging")

    def test_finish_unlocks_and_skips_rest_of_day(self):
        self.at("18:05")
        self.assertTrue(self.finish_session()["ok"])
        self.assertFalse(self.at("18:06")["locked"])
        self.assertTrue(self.svc.state["trained_today"])
        self.assertEqual(self.svc.store.recent_locks()[0]["outcome"], "completed")

    def test_override_needs_phrase_then_waits(self):
        self.at("18:00")
        self.assertFalse(self.svc.command({"cmd": "override", "phrase": "let me out"})["ok"])
        self.assertTrue(self.svc.command({"cmd": "override", "phrase": "i am choosing  to skip my training TODAY"})["ok"])
        self.assertTrue(self.at("18:04")["locked"])
        self.assertFalse(self.at("18:05")["locked"])
        self.assertEqual(self.svc.store.recent_locks()[0]["outcome"], "override")
        self.assertFalse(self.at("18:10")["locked"])  # stays ended
        status = self.svc.store.db.execute("SELECT status FROM sessions").fetchone()[0]
        self.assertEqual(status, "overridden")

    def test_cancelled_override_keeps_lock(self):
        self.at("18:00")
        self.svc.command({"cmd": "override", "phrase": "I am choosing to skip my training today"})
        self.svc.command({"cmd": "cancel-override"})
        self.assertTrue(self.at("18:06")["locked"])

    def test_waits_for_omarchy_lock(self):
        self.desktop.omarchy_locked = True
        state = self.at("18:00")
        self.assertFalse(state["locked"])
        self.assertTrue(state["waiting_for_omarchy_lock"])
        self.desktop.omarchy_locked = False
        self.assertTrue(self.at("18:20")["locked"])

    def test_restores_previous_audio_and_idle(self):
        self.desktop.playing = []
        self.desktop.stay_awake = True
        self.at("18:00")
        self.at("18:30")
        self.assertEqual(self.desktop.playing, [])
        self.assertTrue(self.desktop.stay_awake)

    def test_config_change_frozen_during_lock(self):
        self.at("18:00")
        (self.tmp / "config.toml").write_text(CONFIG.replace("minutes = 30", "minutes = 5"))
        state = self.at("18:10")
        self.assertTrue(state["locked"])
        self.assertTrue(state["config_pending"])
        self.at("18:30")
        self.assertFalse(self.svc.config_pending)
        self.assertEqual(self.svc.config.locks[0].minutes, 5)

    def test_test_lock_not_overridable(self):
        self.at("12:00")
        self.svc.command({"cmd": "test"})
        self.assertTrue(self.at("12:00")["locked"])
        self.assertFalse(self.svc.command({"cmd": "override", "phrase": "I am choosing to skip my training today"})["ok"])
        self.now = self.now.replace(minute=1, second=1)
        self.svc.tick()
        self.assertFalse(self.svc.state["locked"])


    def test_manual_session_locks_and_counts(self):
        self.at("12:00")
        self.assertFalse(self.svc.command({"cmd": "start", "minutes": 5})["ok"])
        self.assertTrue(self.svc.command({"cmd": "start", "minutes": 20})["ok"])
        state = self.at("12:01")
        self.assertEqual(state["lock"]["kind"], "manual")
        self.assertTrue(self.finish_session()["ok"])
        self.assertFalse(self.at("12:02")["locked"])
        self.assertFalse(self.at("18:00")["locked"])  # trained today, scheduled lock skipped

    def test_test_session_does_not_count_as_training(self):
        self.at("12:00")
        self.svc.command({"cmd": "test"})
        self.at("12:00")
        self.assertTrue(self.finish_session()["ok"])
        self.assertFalse(self.at("12:00")["locked"])
        self.assertTrue(self.at("18:00")["locked"])


    def test_scheduled_locks_wait_for_onboarding(self):
        self.svc.store.delete("profile")
        self.assertFalse(self.at("18:00")["locked"])
        self.assertFalse(self.svc.state["setup"]["profile"])
        self.svc.command({"cmd": "profile-save", "profile": {"experience": "beginner", "goals": ["strength"]}})
        self.assertTrue(self.at("18:05")["locked"])

    def test_profile_validation(self):
        bad = self.svc.command({"cmd": "profile-save", "profile": {"experience": "expert", "goals": []}})
        self.assertFalse(bad["ok"])


if __name__ == "__main__":
    unittest.main()

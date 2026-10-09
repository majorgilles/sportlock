"""Fakes for the outside world (desktop, screens, coach, clock) and `World`, a whole app wired
through the real composition root on a temporary SQLite database. Repositories are never mocked:
the real SQLite adapters on a temp file are fast and exact (see the ADR on fakes over mocks)."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
from pathlib import Path

from sportlock.app.container import Container
from sportlock.app.daemon import Daemon
from sportlock.app.socket_api import handle
from sportlock.coaching.domain.ports import CoachUnavailableError
from sportlock.exercises.domain.catalogue import Catalogue
from sportlock.shared_kernel.time import iso

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
HOUSE = ["chair", "table", "bench", "doorway"]


class FakeDesktop:
    """Records notifications; media and idle state as plain attributes."""

    def __init__(self) -> None:
        self.playing = ["org.mpris.MediaPlayer2.spotify"]
        self.stay_awake = False
        self.system_locked = False
        self.notifications: list[str] = []

    def notify(self, headline: str, description: str = "", *, urgent: bool = False) -> None:
        self.notifications.append(headline)

    def pause_media(self) -> list[str]:
        paused, self.playing = self.playing, []
        return list(paused)

    def resume_media(self, players: list[str]) -> None:
        self.playing += players

    def idle_stay_awake(self) -> bool | None:
        return self.stay_awake

    def set_idle_stay_awake(self, stay_awake: bool) -> None:
        self.stay_awake = stay_awake

    def system_lock_active(self) -> bool:
        return self.system_locked

    def theme(self) -> dict[str, str]:
        return {}


class FakeLockScreen:
    """Counts how often it was asked to show."""

    def __init__(self) -> None:
        self.shown = 0

    def ensure_shown(self) -> None:
        self.shown += 1


class FakePopup:
    """Records what it showed."""

    def __init__(self) -> None:
        self.shown: list[dict] = []
        self.closed = 0

    def show(self, content: dict) -> None:
        self.shown.append(content)

    def close(self) -> None:
        self.closed += 1


class FakeCoach:
    """Answers with a prepared output (deep-copied), or fails; records the contexts it saw."""

    def __init__(self) -> None:
        self.output: dict | None = None
        self.contexts: list[dict] = []

    def write_plan(self, context: dict) -> dict:
        self.contexts.append(context)
        if self.output is None:
            raise CoachUnavailableError("no answer prepared")
        return copy.deepcopy(self.output)


class FakeClock:
    """A settable clock."""

    def __init__(self, now: datetime) -> None:
        self.time = now

    def now(self) -> datetime:
        return self.time


def item(
    exercise: str,
    sets: int = 3,
    lo: int | None = None,
    hi: int | None = None,
    seconds: int | None = None,
    rest: int = 60,
    note: str = "",
) -> dict:
    """One exercise as the coach writes it."""
    return {
        "exercise": exercise,
        "sets": sets,
        "reps_low": lo,
        "reps_high": hi,
        "seconds": seconds,
        "rest": rest,
        "note": note,
    }


GOOD_PLAN = {
    "rationale": "Push day focus after a solid squat session.",
    "hard": {
        "title": "Push focus",
        "exercises": [
            item("dynamic-warmup", 1, seconds=300, rest=0),
            item("knee-push-up", 3, 8, 12, note="Up a step: 3×12 felt easy"),
            item("bodyweight-squat", 3, 12, 15),
            item("plank", 3, seconds=40, rest=45),
            item("static-stretch", 1, seconds=300, rest=0),
        ],
    },
    "recovery": {
        "title": "Easy mobility",
        "day_type": "mobility",
        "exercises": [
            item("dynamic-warmup", 1, seconds=300, rest=0),
            item("cat-cow", 2, 8, 10, rest=15),
            item("static-stretch", 1, seconds=300, rest=0),
        ],
    },
    "next_lock": {"mode": "auto", "recovery_minutes": None, "reason": ""},
    "recommendations": [],
    "memory": [],
    "ladder_overrides": [],
}


class World:
    """The app on a temp directory, Monday 2026-10-05 17:00, with a beginner profile saved."""

    def __init__(self, tmp: Path, catalogue: Catalogue, *, config: str = CONFIG, profile: bool = True) -> None:
        self.tmp = tmp
        self.config_path = tmp / "config.toml"
        self.config_path.write_text(config)
        self.desktop = FakeDesktop()
        self.lock_screen = FakeLockScreen()
        self.popup = FakePopup()
        self.coach = FakeCoach()
        self.clock = FakeClock(datetime(2026, 10, 5, 17, 0))
        self.c = Container(
            db_path=tmp / "db.sqlite",
            config_path=self.config_path,
            library_dir=tmp / "library",
            desktop=self.desktop,
            lock_screen=self.lock_screen,
            popup=self.popup,
            coach=self.coach,
            clock=self.clock,
            catalogue=catalogue,
        )
        self.daemon = Daemon(self.c, state_path=tmp / "state.json")
        self.daemon.running = False  # no background ticks
        self.daemon.coach_enabled = False  # the coach only runs when a test asks
        if profile:
            assert self.cmd(
                cmd="profile-save", profile={"experience": "beginner", "goals": ["general fitness"], "equipment": HOUSE}
            )["ok"]

    def at(self, hhmm: str, *, second: int = 0) -> dict:
        """Move the clock to that time today, tick, return the state file's content."""
        hours, minutes = map(int, hhmm.split(":"))
        self.clock.time = self.clock.time.replace(hour=hours, minute=minutes, second=second)
        self.daemon.tick()
        assert self.daemon.state is not None
        return self.daemon.state

    def cmd(self, **request) -> dict:
        """One socket API request."""
        return handle(self.daemon, request)

    def train(self, action: str, **args) -> dict:
        """One training action."""
        return self.cmd(cmd="train", action=action, **args)

    def finish_session(self) -> dict:
        """Skip what's left and finish."""
        while self.daemon.c.sessions.active().phase != "summary":
            assert self.train("skip", reason="testing")["ok"]
        return self.train("finish", rpe=5)

    def add_session(
        self,
        finished: datetime,
        *,
        minutes: int = 45,
        rpe: int = 8,
        day_type: str = "hard",
        kind: str = "scheduled",
        status: str = "finished",
    ) -> None:
        """A past session, written straight into the database."""
        started = finished - timedelta(minutes=minutes)
        self.c.database.execute(
            "INSERT INTO sessions (day, started_at, finished_at, kind, status, day_type, rpe) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (finished.date().isoformat(), iso(started), iso(finished), kind, status, day_type, rpe),
        )

    def rest_day(self, day) -> None:
        """A past scheduled lock that became a rest day."""
        key = f"{day.isoformat()}T18:00"
        self.c.database.execute(
            "INSERT INTO lock_events (key, start, end, began_at, ended_at, outcome) VALUES (?, ?, ?, ?, ?, 'rest')",
            (key, key, key, key, key),
        )

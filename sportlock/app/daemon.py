"""The sportlock user service.

Ticks the schedule, keeps the lock screen's state file fresh, runs the coach in the background, and answers the CLI and
the screens over a Unix socket.

The lock screen reads $XDG_RUNTIME_DIR/sportlock/state.json once a second and releases the lock
when `locked` turns false or the file stops being refreshed: stopping this service always unlocks.
"""

from __future__ import annotations

import json
import os
import signal
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from sportlock.app.container import Container
from sportlock.app.socket_api import SocketServer
from sportlock.athlete.application.profile_services import equipment_of
from sportlock.coaching.application.run_coach_service import RunCoachCommand
from sportlock.coaching.domain.plan import CoachOutputError
from sportlock.coaching.domain.ports import CoachUnavailableError
from sportlock.locks.application.lock_services import policy_of
from sportlock.locks.application.tick_services import restore_desktop
from sportlock.locks.domain.schedule import Decision, settings_frozen, windows_around
from sportlock.music.application.music_account_services import ConnectMusicAccountCommand
from sportlock.music.domain.music import MusicError, client_id_of
from sportlock.reporting.application.report_services import ReviewError
from sportlock.shared_kernel.infrastructure.logging_setup import setup_logging
from sportlock.shared_kernel.time import epoch_ms

RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "sportlock"
STATE_PATH = RUNTIME_DIR / "state.json"
SOCKET_PATH = RUNTIME_DIR / "sock"
TICK_SECONDS = 1
COACH_RETRY = timedelta(minutes=30)
log = setup_logging()


class Daemon:
    """One service process; every request and tick runs under one lock (`mutex`)."""

    def __init__(self, container: Container | None = None, *, state_path: Path = STATE_PATH) -> None:
        self.state_path = state_path
        self.container = container or Container(state_path=state_path)
        self.mutex = threading.RLock()
        self.running = True
        self.coach_enabled = True
        self.coach_thread: threading.Thread | None = None
        self.review_thread: threading.Thread | None = None
        self.review_error = ""
        self.coach_retry_at: datetime | None = None
        self.state: dict | None = None
        self.music_login_thread: threading.Thread | None = None
        self.music_login_error = ""

    # -- the tick ----------------------------------------------------------------------------

    def tick(self) -> None:
        """Reload settings, back up, lock or unlock, refresh the state file, maybe run the coach."""
        with self.mutex:
            now = self.container.clock.now()
            self.container.reload_settings_service.execute(
                decision=self.container.get_schedule_decision_service.execute(now), now=now
            )
            self.container.database.backup_daily(now.date())
            decision = self.container.run_lock_tick_service.execute(now)
            self.write_state(now, decision)
            self.maybe_run_coach(now)

    def schedule_tick(self) -> None:
        """Tick soon (after a command), without waiting for the loop."""
        if self.running:
            threading.Thread(target=self.tick, daemon=True).start()

    # -- the coach ---------------------------------------------------------------------------

    def coach_command(self, now: datetime) -> RunCoachCommand:
        """What the coach should plan for: the next scheduled locks and the rest guardrails."""
        settings = self.container.settings_state.settings
        upcoming = (
            [
                {"start": w.start.isoformat(timespec="minutes"), "minutes": w.minutes}
                for w in windows_around(settings, now)
                if w.start > now
            ][:3]
            if settings.enabled
            else []
        )
        policy = policy_of(settings)
        return RunCoachCommand(
            equipment=equipment_of(self.container.profile_repository, settings),
            upcoming_locks=tuple(upcoming),
            rest_policy={
                "allow_rest_days": policy.allow_rest_days,
                "max_rest_days_in_a_row": policy.max_rest_days_in_a_row,
                "min_sessions_per_week": policy.min_sessions_per_week,
                "default_recovery_minutes": policy.recovery_minutes,
            },
        )

    def coach_running(self) -> bool:
        """Whether a coach run is in progress."""
        return bool(self.coach_thread and self.coach_thread.is_alive())

    def maybe_run_coach(self, now: datetime) -> None:
        """Start a background coach run when the plan is stale (not while one runs or waits to retry)."""
        if not self.coach_enabled or self.coach_running() or (self.coach_retry_at and now < self.coach_retry_at):
            return
        if not self.container.coach_plan_freshness.needs_run():
            return
        self.coach_thread = threading.Thread(target=self._run_coach, args=(self.coach_command(now),), daemon=True)
        self.coach_thread.start()

    def _run_coach(self, command: RunCoachCommand) -> None:
        # Claude can take minutes: run without holding the mutex; SQLite is safe to share.
        try:
            plan = self.container.run_coach_service.execute(command)
            log.info("coach planned: hard=%s recovery=%s", plan.hard.title, plan.recovery.title)
            self.coach_retry_at = None
            self._write_review(command)  # the Report tab's dossier and advice, after the plan
        except (CoachUnavailableError, CoachOutputError) as error:
            log.warning("coach failed: %s", error)
            self.coach_retry_at = self.container.clock.now() + COACH_RETRY
            self.container.desktop.notify(
                "Coach couldn't plan your next session", f"{error}. Using the built-in planner; retrying in 30 min."
            )

    def review_running(self) -> bool:
        """Whether the coach is writing the report on request."""
        return bool(self.review_thread and self.review_thread.is_alive())

    def start_review(self, now: datetime) -> None:
        """Ask the coach to rewrite the report now, in the background."""
        if self.review_running() or self.coach_running():
            raise ReviewError("the coach is already working; the report updates when it's done")
        if self.container.training_history.last_counted_id() is None:
            raise ReviewError("train once first: the coach reviews your sessions")
        self.review_error = ""
        self.review_thread = threading.Thread(target=self._write_review, args=(self.coach_command(now),), daemon=True)
        self.review_thread.start()

    def _write_review(self, command: RunCoachCommand) -> None:
        try:
            self.container.write_coach_review_service.execute(command)
            log.info("coach reviewed the trainee")
            self.review_error = ""
        except (CoachUnavailableError, CoachOutputError, ReviewError) as error:
            log.warning("coach review failed: %s", error)
            self.review_error = str(error)

    # -- the Spotify login -------------------------------------------------------------------

    def music_login_running(self) -> bool:
        """Whether a browser login to Spotify is waiting for the user."""
        return bool(self.music_login_thread and self.music_login_thread.is_alive())

    def start_music_login(self, client_id: str) -> None:
        """Log in to Spotify in the background (the user allows access in the browser)."""
        client_id_of(client_id)  # a bad Client ID is refused right away
        if self.music_login_running():
            raise MusicError("already waiting for Spotify in the browser")
        self.music_login_error = ""
        self.music_login_thread = threading.Thread(target=self._music_login, args=(client_id,), daemon=True)
        self.music_login_thread.start()

    def _music_login(self, client_id: str) -> None:
        try:
            name = self.container.connect_music_account_service.execute(ConnectMusicAccountCommand(client_id=client_id))
            log.info("spotify: connected as %s", name)
        except (MusicError, OSError) as error:
            log.warning("spotify login failed: %s", error)
            self.music_login_error = str(error) if isinstance(error, MusicError) else "Spotify can't be reached"

    # -- state for the screens and the bar ---------------------------------------------------

    def write_state(self, now: datetime, decision: Decision) -> None:
        """The state-file contract read by locks/ui/lock_screen and app/window."""
        lock = self.container.lock_runtime.current
        settings = self.container.settings_state.settings
        override = None
        if lock and lock.overridable and (pending := self.container.override_repository.pending(lock.key)):
            override = {"unlock_at": epoch_ms(pending.unlock_at)}
        state = {
            "updated_at": epoch_ms(datetime.now()),
            "locked": lock is not None,
            "waiting_for_omarchy_lock": self.container.lock_runtime.waiting_for_omarchy,
            "lock": lock
            and {
                "key": lock.key,
                "kind": lock.kind,
                "start": epoch_ms(lock.window.start),
                "end": epoch_ms(lock.window.end),
                "overridable": lock.overridable,
                "test": lock.test,
            },
            "override": override,
            "override_phrase": settings.override_phrase,
            "override_wait_seconds": settings.override_wait_seconds,
            "next_lock": decision.next and {"start": epoch_ms(decision.next.start), "end": epoch_ms(decision.next.end)},
            "trained_today": now.date() in self.container.training_history.trained_days(),
            "training": self.container.training_snapshot_service.execute() if lock else None,
            "music": self.container.music_snapshot_service.execute() if lock else None,
            "replacements": [r.to_dict() for r in self.container.pending_replacements_service.execute()]
            if lock
            else [],
            "theme": self.container.desktop.theme(),
            "setup": {
                "profile": self.container.check_setup_complete_service.execute(),
                "agent_running": self.coach_running(),
                "plan_ready": self.container.coach_plan_freshness.fresh_plan() is not None,
            },
            "config_error": self.container.settings_state.error,
            "config_pending": self.container.settings_state.pending,
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        tmp.replace(self.state_path)
        self.state = state

    def frozen(self, now: datetime) -> bool:
        """Whether settings edits wait for the current lock."""
        return settings_frozen(self.container.get_schedule_decision_service.execute(now), now)

    # -- lifecycle ---------------------------------------------------------------------------

    def run(self) -> None:
        """Serve until SIGTERM/SIGINT; leaving always unlocks."""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        if self.container.lock_runtime.current is None:
            restore_desktop(
                self.container.lock_state_repository, self.container.desktop, self.container.music_player
            )  # left over from a crash mid-lock
        server = SocketServer(self, SOCKET_PATH)
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def stop(*_: object) -> None:
            """Signal handler: finish the current tick, then leave."""
            self.running = False

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        while self.running:
            try:
                self.tick()
            except Exception:  # keep ticking; a dead service would fail open anyway
                log.exception("tick failed")
            time.sleep(TICK_SECONDS)
        with self.mutex:
            now = self.container.clock.now()
            self.container.leave_lock_service.execute(now)
            self.write_state(now, self.container.get_schedule_decision_service.execute(now))
        server.shutdown()
        SOCKET_PATH.unlink(missing_ok=True)


def main() -> None:
    """Entry point of `sportlock service`."""
    Daemon().run()

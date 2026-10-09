"""The sportlock user service: ticks the schedule, keeps the lock screen's state file fresh, runs
the coach in the background, and answers the CLI and the screens over a Unix socket.

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
from sportlock.athlete.application.profile_services import equipment_of
from sportlock.coaching.application.run_coach_service import RunCoachCommand
from sportlock.coaching.domain.plan import CoachOutputError
from sportlock.coaching.domain.ports import CoachUnavailableError
from sportlock.locks.application.lock_services import policy_of, restore_desktop
from sportlock.locks.domain.schedule import Decision, settings_frozen, windows_around
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
        self.c = container or Container(state_path=state_path)
        self.mutex = threading.RLock()
        self.running = True
        self.coach_enabled = True
        self.coach_thread: threading.Thread | None = None
        self.coach_retry_at: datetime | None = None
        self.state: dict | None = None

    # -- the tick ----------------------------------------------------------------------------

    def tick(self) -> None:
        """Reload settings, back up, lock or unlock, refresh the state file, maybe run the coach."""
        with self.mutex:
            now = self.c.clock.now()
            self.c.reload_settings.execute(decision=self.c.tick.decision(now), now=now)
            self.c.database.backup_daily(now.date())
            decision = self.c.tick.execute(now)
            self.write_state(now, decision)
            self.maybe_run_coach(now)

    def schedule_tick(self) -> None:
        """Tick soon (after a command), without waiting for the loop."""
        if self.running:
            threading.Thread(target=self.tick, daemon=True).start()

    # -- the coach ---------------------------------------------------------------------------

    def coach_command(self, now: datetime) -> RunCoachCommand:
        """What the coach should plan for: the next scheduled locks and the rest guardrails."""
        settings = self.c.settings.settings
        upcoming = [{"start": w.start.isoformat(timespec="minutes"), "minutes": w.minutes}
                    for w in windows_around(settings, now) if w.start > now][:3] if settings.enabled else []
        policy = policy_of(settings)
        return RunCoachCommand(equipment=equipment_of(self.c.profiles, settings), upcoming_locks=tuple(upcoming),
                               rest_policy={"allow_rest_days": policy.allow_rest_days,
                                            "max_rest_days_in_a_row": policy.max_rest_days_in_a_row,
                                            "min_sessions_per_week": policy.min_sessions_per_week,
                                            "default_recovery_minutes": policy.recovery_minutes})

    def coach_running(self) -> bool:
        """Whether a coach run is in progress."""
        return bool(self.coach_thread and self.coach_thread.is_alive())

    def maybe_run_coach(self, now: datetime) -> None:
        """Start a background coach run when the plan is stale (not while one runs or waits to retry)."""
        if not self.coach_enabled or self.coach_running() or (self.coach_retry_at and now < self.coach_retry_at):
            return
        if not self.c.freshness.needs_run():
            return
        self.coach_thread = threading.Thread(target=self._run_coach, args=(self.coach_command(now),), daemon=True)
        self.coach_thread.start()

    def _run_coach(self, command: RunCoachCommand) -> None:
        # Claude can take minutes: run without holding the mutex; SQLite is safe to share.
        try:
            plan = self.c.run_coach.execute(command)
            log.info("coach planned: hard=%s recovery=%s", plan.hard.title, plan.recovery.title)
            self.coach_retry_at = None
        except (CoachUnavailableError, CoachOutputError) as error:
            log.warning("coach failed: %s", error)
            self.coach_retry_at = self.c.clock.now() + COACH_RETRY
            self.c.desktop.notify("Coach couldn't plan your next session",
                                  f"{error}. Using the built-in planner; retrying in 30 min.")

    # -- state for the screens and the bar ---------------------------------------------------

    def write_state(self, now: datetime, decision: Decision) -> None:
        """The state-file contract read by locks/ui/lock_screen and app/window."""
        lock = self.c.runtime.current
        settings = self.c.settings.settings
        override = None
        if lock and lock.overridable and (pending := self.c.overrides.pending(lock.key)):
            override = {"unlock_at": epoch_ms(pending.unlock_at)}
        state = {
            "updated_at": epoch_ms(datetime.now()),
            "locked": lock is not None,
            "waiting_for_omarchy_lock": self.c.runtime.waiting_for_omarchy,
            "lock": lock and {"key": lock.key, "kind": lock.kind, "start": epoch_ms(lock.window.start),
                              "end": epoch_ms(lock.window.end), "overridable": lock.overridable, "test": lock.test},
            "override": override,
            "override_phrase": settings.override_phrase,
            "override_wait_seconds": settings.override_wait_seconds,
            "next_lock": decision.next and {"start": epoch_ms(decision.next.start), "end": epoch_ms(decision.next.end)},
            "trained_today": now.date() in self.c.history.trained_days(),
            "training": self.c.training_snapshot.execute() if lock else None,
            "theme": self.c.desktop.theme(),
            "setup": {"profile": self.c.tick.setup_complete(), "agent_running": self.coach_running(),
                      "plan_ready": self.c.freshness.fresh_plan() is not None},
            "config_error": self.c.settings.error,
            "config_pending": self.c.settings.pending,
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        tmp.replace(self.state_path)
        self.state = state

    def frozen(self, now: datetime) -> bool:
        """Whether settings edits wait for the current lock."""
        return settings_frozen(self.c.tick.decision(now), now)

    # -- lifecycle ---------------------------------------------------------------------------

    def run(self) -> None:
        """Serve until SIGTERM/SIGINT; leaving always unlocks."""
        from sportlock.app.socket_api import SocketServer

        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        if self.c.runtime.current is None:
            restore_desktop(self.c.lock_state, self.c.desktop)  # left over from a crash mid-lock
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
            now = self.c.clock.now()
            self.c.tick.release(now)
            self.write_state(now, self.c.tick.decision(now))
        server.shutdown()
        SOCKET_PATH.unlink(missing_ok=True)


def main() -> None:
    """Entry point of `sportlock service`."""
    Daemon().run()

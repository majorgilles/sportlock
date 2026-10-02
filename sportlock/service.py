"""The sportlock user service: ticks the schedule, drives the locker, answers the CLI.

The locker (a separate Quickshell process holding the Wayland session lock) reads
$XDG_RUNTIME_DIR/sportlock/state.json once a second and releases the lock when `locked` turns
false or the file stops being refreshed. Stopping this service therefore always unlocks.
"""

from __future__ import annotations

import json
import os
import signal
import socketserver
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from . import config as config_mod
from . import system
from .schedule import Window, config_frozen, decide
from .store import Store
from .training import Training, TrainingError

RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "sportlock"
STATE_PATH = RUNTIME_DIR / "state.json"
SOCKET_PATH = RUNTIME_DIR / "sock"
LOCKER_DIR = Path(__file__).resolve().parent.parent / "locker"
TEST_SECONDS = 60
TICK_SECONDS = 1

MANUAL_MINUTES = (10, 90)
TRAIN_ACTIONS = {
    "start_set": (), "stop_set": (), "end_sets": (), "swap_easier": (),
    "save_set": ("reps", "load_kg"), "rate": ("rpe", "note"), "skip": ("reason",),
    "finish": ("rpe", "notes", "calories", "avg_hr", "body_weight"),
}


def now_local() -> datetime:
    return datetime.now().replace(microsecond=0)


def _epoch_ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


@dataclass
class ActiveLock:
    key: str
    window: Window
    kind: str  # scheduled | manual | test

    @property
    def overridable(self) -> bool:
        return self.kind != "test"

    @property
    def test(self) -> bool:
        return self.kind == "test"


class Service:
    def __init__(self, store: Store | None = None, config_path: Path | None = None) -> None:
        self.mutex = threading.RLock()
        self.store = store or Store()
        self.training = Training(self.store)
        self.config_path = config_path or config_mod.CONFIG_PATH
        self.config = config_mod.Config()
        self.config_mtime = 0.0
        self.config_error: str | None = None
        self.config_pending = False
        self.test_lock: ActiveLock | None = None
        self.current: ActiveLock | None = None  # lock the screen is (or should be) under
        self.waiting_for_omarchy = False
        self.locker: subprocess.Popen | None = None
        self.running = True
        self._load_config(force=True)

    # -- config --------------------------------------------------------------------------------

    def _load_config(self, *, force: bool = False, now: datetime | None = None) -> None:
        path = self.config_path
        try:
            mtime = path.stat().st_mtime if path.exists() else 0.0
        except OSError:
            return
        if not force and mtime == self.config_mtime and not self.config_pending:
            return

        if not force and now is not None and config_frozen(self._decision(now), now):
            if not self.config_pending:
                self.config_pending = True
            return

        try:
            self.config = config_mod.load(path)
            self.config_error = None
        except config_mod.ConfigError as error:
            if self.config_error != str(error):
                system.notify("sportlock config error", str(error), urgent=True)
            self.config_error = str(error)
        self.config_mtime = path.stat().st_mtime if path.exists() else 0.0
        self.config_pending = False

    # -- schedule ------------------------------------------------------------------------------

    def _decision(self, now: datetime):
        return decide(self.config, now, trained_days=self.store.trained_days(), ended=self.store.ended_early())

    def _wanted_lock(self, now: datetime, decision) -> ActiveLock | None:
        candidates = []
        if self.test_lock and now >= self.test_lock.window.end:
            self.test_lock = None
        if self.test_lock:
            candidates.append(self.test_lock)

        manual = self.store.get("manual_lock")
        if manual:
            window = Window(datetime.fromisoformat(manual["start"]), datetime.fromisoformat(manual["end"]))
            if now < window.end and manual["key"] not in self.store.ended_early():
                candidates.append(ActiveLock(manual["key"], window, "manual"))
            else:
                self.store.delete("manual_lock")

        if decision.active:
            candidates.append(ActiveLock(decision.active.key, decision.active, "scheduled"))

        # Stay under the lock already on screen while it is still due; overlaps don't swap sessions.
        for lock in candidates:
            if self.current and lock.key == self.current.key:
                return lock
        return candidates[0] if candidates else None

    def tick(self) -> None:
        with self.mutex:
            now = now_local()
            self._load_config(now=now)
            self.store.backup_daily(now.date())
            self._settle_override(now)

            decision = self._decision(now)
            wanted = self._wanted_lock(now, decision)
            self._warn(decision, wanted)

            if self.current and (wanted is None or wanted.key != self.current.key):
                self._leave_lock(now)
            if wanted and self.current is None:
                self._enter_lock(wanted, now)
            if self.current:
                self._ensure_locker()

            self._write_state(now, decision)

    def _settle_override(self, now: datetime) -> None:
        if not self.current or not self.current.overridable:
            return
        pending = self.store.pending_override(self.current.key)
        if pending and now >= datetime.fromisoformat(pending["unlock_at"]):
            self.store.finish_override(pending["id"], now, cancelled=False)
            self.store.lock_ended(self.current.key, "override", now)

    def _warn(self, decision, wanted: ActiveLock | None) -> None:
        if wanted or decision.next is None or decision.warning is None:
            return
        tag = f"{decision.next.key}:{decision.warning}"
        warned = self.store.get("warned", [])
        if tag in warned:
            return
        start = decision.next.start.strftime("%H:%M")
        system.notify(
            f"Training lock at {start}",
            f"Your desktop locks in {decision.warning} min for {int((decision.next.end - decision.next.start).total_seconds() // 60)} min.",
            urgent=decision.warning <= 2,
        )
        self.store.put("warned", (warned + [tag])[-50:])

    # -- locking -------------------------------------------------------------------------------

    def _enter_lock(self, lock: ActiveLock, now: datetime) -> None:
        if system.omarchy_lock_active():
            # The countdown keeps running; take over as soon as the user unlocks.
            self.waiting_for_omarchy = True
            return
        self.waiting_for_omarchy = False

        if self.store.get("restore") is None:
            self.store.put("restore", {"paused": system.pause_media(), "stay_awake": system.idle_stay_awake()})
        system.set_idle_stay_awake(True)

        if not lock.test:
            self.store.lock_began(lock.key, lock.window.start, lock.window.end, now)
        self.training.begin(now=now, kind=lock.kind, lock_key=lock.key,
                            minutes=(lock.window.end - now).total_seconds() / 60)
        self.current = lock

    def _leave_lock(self, now: datetime) -> None:
        lock = self.current
        self.current = None
        if lock and self.training.run is not None:
            outcome = self._outcome(lock.key)
            self.training.close(now=now, status="overridden" if outcome == "override" else "abandoned")
        if lock and not lock.test:
            self.store.lock_ended(lock.key, "expired", now)  # no-op if already ended early
        self._restore()

    def _outcome(self, key: str) -> str | None:
        row = self.store.db.execute("SELECT outcome FROM lock_events WHERE key = ?", (key,)).fetchone()
        return row["outcome"] if row else None

    def _restore(self) -> None:
        saved = self.store.get("restore")
        if saved is None:
            return
        system.resume_media(saved.get("paused") or [])
        if saved.get("stay_awake") is not None:
            system.set_idle_stay_awake(bool(saved["stay_awake"]))
        self.store.delete("restore")

    def _ensure_locker(self) -> None:
        if self.locker and self.locker.poll() is None:
            return
        env = dict(os.environ, SPORTLOCK_STATE=str(STATE_PATH), SPORTLOCK_BIN=str(LOCKER_DIR.parent / "bin" / "sportlock"))
        self.locker = subprocess.Popen(
            ["qs", "-p", str(LOCKER_DIR)], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )

    # -- state for the locker and bar ----------------------------------------------------------

    def _write_state(self, now: datetime, decision) -> None:
        lock = self.current
        override = None
        if lock and lock.overridable:
            pending = self.store.pending_override(lock.key)
            if pending:
                override = {"unlock_at": _epoch_ms(datetime.fromisoformat(pending["unlock_at"]))}

        state = {
            "updated_at": _epoch_ms(datetime.now()),
            "locked": lock is not None,
            "waiting_for_omarchy_lock": self.waiting_for_omarchy,
            "lock": lock and {
                "key": lock.key,
                "kind": lock.kind,
                "start": _epoch_ms(lock.window.start),
                "end": _epoch_ms(lock.window.end),
                "overridable": lock.overridable,
                "test": lock.test,
            },
            "override": override,
            "override_phrase": self.config.override_phrase,
            "override_wait_seconds": self.config.override_wait_seconds,
            "next_lock": decision.next and {
                "start": _epoch_ms(decision.next.start),
                "end": _epoch_ms(decision.next.end),
            },
            "trained_today": now.date() in self.store.trained_days(),
            "training": self.training.snapshot() if lock else None,
            "theme": system.theme(),
            "config_error": self.config_error,
            "config_pending": self.config_pending,
        }
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        tmp.replace(STATE_PATH)
        self.state = state

    # -- commands (from the CLI / locker over the socket) --------------------------------------

    def command(self, request: dict) -> dict:
        with self.mutex:
            now = now_local()
            cmd = request.get("cmd")

            if cmd == "status":
                return {"ok": True, "state": getattr(self, "state", None)}

            if cmd == "test":
                if self.current:
                    return {"ok": False, "error": "a lock is already active"}
                window = Window(now, now + timedelta(seconds=TEST_SECONDS))
                self.test_lock = ActiveLock(f"test-{now.isoformat()}", window, "test")
                self._schedule_tick()
                return {"ok": True}

            if cmd == "start":
                if self.current:
                    return {"ok": False, "error": "a lock is already active"}
                minutes = int(request.get("minutes", 30))
                if not MANUAL_MINUTES[0] <= minutes <= MANUAL_MINUTES[1]:
                    return {"ok": False, "error": f"minutes must be between {MANUAL_MINUTES[0]} and {MANUAL_MINUTES[1]}"}
                end = now + timedelta(minutes=minutes)
                self.store.put("manual_lock", {"key": f"manual-{now.isoformat()}", "start": now.isoformat(),
                                               "end": end.isoformat()})
                self._schedule_tick()
                return {"ok": True}

            if cmd == "override":
                lock = self.current
                if not lock:
                    return {"ok": False, "error": "no active lock"}
                if not lock.overridable:
                    return {"ok": False, "error": "this lock can't be overridden"}
                if " ".join(str(request.get("phrase", "")).split()).lower() != self.config.override_phrase.lower():
                    return {"ok": False, "error": "phrase doesn't match"}
                if not self.store.pending_override(lock.key):
                    unlock_at = now + timedelta(seconds=self.config.override_wait_seconds)
                    self.store.start_override(lock.key, now, unlock_at)
                self._schedule_tick()
                return {"ok": True}

            if cmd == "cancel-override":
                if self.current and (pending := self.store.pending_override(self.current.key)):
                    self.store.finish_override(pending["id"], now, cancelled=True)
                self._schedule_tick()
                return {"ok": True}

            if cmd == "train":
                return self._train(request, now)

            if cmd == "log":
                return {"ok": True, "locks": self.store.recent_locks(int(request.get("limit", 20)))}

            if cmd == "reload":
                self._load_config(force=not self.config_pending, now=now)
                return {"ok": True, "pending": self.config_pending, "error": self.config_error}

            return {"ok": False, "error": f"unknown command {cmd!r}"}

    def _train(self, request: dict, now: datetime) -> dict:
        action = request.get("action")
        if action not in TRAIN_ACTIONS:
            return {"ok": False, "error": f"unknown training action {action!r}"}
        args = {name: request[name] for name in TRAIN_ACTIONS[action] if request.get(name) not in (None, "")}
        try:
            getattr(self.training, action)(now=now, **args)
        except (TrainingError, TypeError, ValueError) as error:
            return {"ok": False, "error": str(error)}

        if action == "finish" and (lock := self.current):
            if lock.test:
                self.test_lock = None
            else:
                self.store.lock_ended(lock.key, "completed", now)
        self._schedule_tick()
        return {"ok": True}

    def _schedule_tick(self) -> None:
        if self.running:
            threading.Thread(target=self.tick, daemon=True).start()

    # -- lifecycle -----------------------------------------------------------------------------

    def run(self) -> None:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        if self.current is None:
            self._restore()  # left over from a crash mid-lock

        server = _SocketServer(self)
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def stop(*_):
            self.running = False

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)

        while self.running:
            try:
                self.tick()
            except Exception as error:  # keep ticking; a dead service would fail open anyway
                print(f"sportlock: tick failed: {error!r}", flush=True)
            time.sleep(TICK_SECONDS)

        with self.mutex:
            if self.current:
                self._leave_lock(now_local())
            self._write_state(now_local(), self._decision(now_local()))
        server.shutdown()
        SOCKET_PATH.unlink(missing_ok=True)


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            request = json.loads(self.rfile.readline() or b"{}")
            response = self.server.service.command(request)
        except Exception as error:
            response = {"ok": False, "error": repr(error)}
        self.wfile.write(json.dumps(response).encode() + b"\n")


class _SocketServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, service: Service):
        SOCKET_PATH.unlink(missing_ok=True)
        super().__init__(str(SOCKET_PATH), _Handler)
        os.chmod(SOCKET_PATH, 0o600)
        self.service = service


def main() -> None:
    Service().run()

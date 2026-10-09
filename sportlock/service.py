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
from .schedule import Window, config_frozen, decide, windows_around
from .store import Store
from . import diagnostics
from . import recovery
from . import profile as profile_mod
from .agent import Agent, AgentError
from .training import Training, TrainingError

RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "sportlock"
STATE_PATH = RUNTIME_DIR / "state.json"
SOCKET_PATH = RUNTIME_DIR / "sock"
LOCKER_DIR = Path(__file__).resolve().parent.parent / "locker"
POPUP_DIR = Path(__file__).resolve().parent.parent / "popup"
TEST_SECONDS = 60
TICK_SECONDS = 1
log = diagnostics.setup_logging()

MANUAL_MINUTES = (10, 90)
TRAIN_ACTIONS = {
    "start_set": (), "stop_set": (), "end_sets": (), "swap_easier": (), "go_now": (), "cancel_set": (),
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
    mode: str | None = None  # hard | recovery, as planned for a scheduled lock

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
        self.agent_thread: threading.Thread | None = None
        self.agent_retry_at: datetime | None = None
        self.agent_enabled = True
        self.config_path = config_path or config_mod.CONFIG_PATH
        self.config = config_mod.Config()
        self.config_mtime = 0.0
        self.config_error: str | None = None
        self.config_pending = False
        self.test_lock: ActiveLock | None = None
        self.current: ActiveLock | None = None  # lock the screen is (or should be) under
        self.waiting_for_omarchy = False
        self.locker: subprocess.Popen | None = None
        self.popup: subprocess.Popen | None = None
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

        if decision.active and self.setup_complete():
            plan = self._lock_plan(decision.active, now)
            if plan["mode"] == "rest":
                self._record_rest(decision.active, plan, now)
            else:
                end = datetime.fromisoformat(plan["end"])  # recovery locks can be shorter
                if now < end:
                    candidates.append(ActiveLock(decision.active.key, Window(decision.active.start, end),
                                                 "scheduled", mode=plan["mode"]))

        # Stay under the lock already on screen while it is still due; overlaps don't swap sessions.
        for lock in candidates:
            if self.current and lock.key == self.current.key:
                return lock
        return candidates[0] if candidates else None

    # -- what each scheduled lock should be ------------------------------------------------------

    def _policy(self) -> recovery.Policy:
        c = self.config
        return recovery.Policy(c.allow_rest_days, c.max_rest_days_in_a_row, c.min_sessions_per_week, c.recovery_minutes)

    def _agent(self) -> Agent:
        now = now_local()
        upcoming = [{"start": w.start.isoformat(timespec="minutes"), "minutes": int((w.end - w.start).total_seconds() // 60)}
                    for w in windows_around(self.config, now) if w.start > now][:3] if self.config.enabled else []
        p = self._policy()
        return Agent(self.store, self.training.library, self.config.notebook_id, upcoming_locks=upcoming,
                     rest_policy={"allow_rest_days": p.allow_rest_days, "max_rest_days_in_a_row": p.max_rest_days_in_a_row,
                                  "min_sessions_per_week": p.min_sessions_per_week,
                                  "default_recovery_minutes": p.recovery_minutes})

    def _lock_plan(self, window: Window, now: datetime) -> dict:
        """Decide once per scheduled lock (from 10 minutes before it) whether it is hard, recovery
        (maybe shorter) or a rest day; the decision is stored so it survives restarts."""
        plans = self.store.get("lock_plans", {})
        if window.key in plans:
            return plans[window.key]
        fresh = self._agent().fresh_plan()
        minutes = int((window.end - window.start).total_seconds() // 60)
        decided = recovery.plan_lock(self.store, self._policy(), now=window.start, window_minutes=minutes,
                                     coach=fresh.get("next_lock") if fresh else None)
        entry = {"mode": decided.mode, "minutes": decided.minutes, "reason": decided.reason,
                 "end": (window.start + timedelta(minutes=decided.minutes)).isoformat()}
        plans[window.key] = entry
        self.store.put("lock_plans", dict(list(plans.items())[-40:]))
        log.info("lock %s planned as %s (%s min): %s", window.key, decided.mode, decided.minutes, decided.reason)
        if decided.mode == "rest":
            system.notify(f"Rest day: no lock at {window.start.strftime('%H:%M')}", decided.reason)
        return entry

    def _record_rest(self, window: Window, plan: dict, now: datetime) -> None:
        if window.key in self.store.ended_early():
            return
        self.store.lock_began(window.key, window.start, window.end, now)
        self.store.lock_ended(window.key, "rest", now)

    def setup_complete(self) -> bool:
        return profile_mod.load(self.store) is not None

    def equipment(self) -> set[str]:
        return profile_mod.equipment(self.store, self.config.equipment)

    # -- agent ---------------------------------------------------------------------------------

    def _maybe_run_agent(self, now: datetime) -> None:
        if not self.agent_enabled or (self.agent_thread and self.agent_thread.is_alive()):
            return
        if self.agent_retry_at and now < self.agent_retry_at:
            return
        agent = self._agent()
        if not agent.needs_run():
            return
        equipment = self.equipment()
        self.agent_thread = threading.Thread(target=self._run_agent, args=(agent, now, equipment), daemon=True)
        self.agent_thread.start()

    def _run_agent(self, agent: Agent, now: datetime, equipment: set[str]) -> None:
        # Claude can take minutes: run without holding the mutex; the store is safe to share.
        try:
            plan = agent.run(now, equipment)
            log.info("coach planned: hard=%s recovery=%s", plan["hard"]["title"], plan["recovery"]["title"])
            self.agent_retry_at = None
            if plan["recommendations"]:
                system.notify("Coach's feedback on your session",
                              "\n".join(f"• {r['advice']}" for r in plan["recommendations"]))
            else:
                system.notify("Next session planned", plan.get("rationale") or plan["hard"]["title"])
        except AgentError as error:
            log.warning("coach failed: %s", error)
            self.agent_retry_at = now_local() + timedelta(minutes=30)
            system.notify("Coach couldn't plan your next session",
                          f"{error}. Using the built-in planner; retrying in 30 min.")

    def tick(self) -> None:
        with self.mutex:
            now = now_local()
            self._load_config(now=now)
            self.store.backup_daily(now.date())
            self._settle_override(now)

            decision = self._decision(now)
            if decision.next and self.setup_complete() and decision.next.start - timedelta(minutes=10) <= now:
                self._lock_plan(decision.next, now)  # decide early so the warning can say what's coming
            wanted = self._wanted_lock(now, decision)
            self._warn(decision, wanted)

            if self.current and (wanted is None or wanted.key != self.current.key):
                self._leave_lock(now)
            if wanted and self.current is None:
                self._enter_lock(wanted, now)
            if self.current:
                self._ensure_locker()

            self._write_state(now, decision)
            self._maybe_run_agent(now)

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
        plan = self.store.get("lock_plans", {}).get(decision.next.key)
        if plan and plan["mode"] == "rest":
            return  # the rest-day notification already went out
        start = decision.next.start.strftime("%H:%M")
        minutes = plan["minutes"] if plan else int((decision.next.end - decision.next.start).total_seconds() // 60)
        kind = "Recovery lock" if plan and plan["mode"] == "recovery" else "Training lock"
        system.notify(
            f"{kind} at {start}",
            f"Your desktop locks in {decision.warning} min for {minutes} min." + (f" {plan['reason']}" if plan and plan["reason"] else ""),
            urgent=decision.warning <= 2,
        )
        # The first warning also gets a popup in front of everything: notifications are easy to miss.
        if self.config.warn_popup and not any(t.startswith(f"{decision.next.key}:") for t in warned):
            self._show_popup(decision.next, plan, minutes, kind)
        self.store.put("warned", (warned + [tag])[-50:])

    def _show_popup(self, window: Window, plan: dict | None, minutes: int, kind: str) -> None:
        coach = self._agent().fresh_plan()
        mode = plan["mode"] if plan else None
        version = coach.get(mode) if coach and mode in ("hard", "recovery") else None
        content = {
            "headline": f"{kind} at {window.start.strftime('%H:%M')}",
            "start": _epoch_ms(window.start),
            "minutes": minutes,
            "title": version["title"] if version else "",
            "reason": plan["reason"] if plan else "",
            "recommendations": coach.get("recommendations", []) if coach else [],
            "theme": system.theme(),
        }
        self._close_popup()
        env = dict(os.environ, SPORTLOCK_POPUP=json.dumps(content))
        try:
            self.popup = subprocess.Popen(["qs", "-p", str(POPUP_DIR)], env=env, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as error:
            log.warning("could not show the warning popup: %s", error)

    def _close_popup(self) -> None:
        if self.popup and self.popup.poll() is None:
            self.popup.terminate()
        self.popup = None

    # -- locking -------------------------------------------------------------------------------

    def _enter_lock(self, lock: ActiveLock, now: datetime) -> None:
        if system.omarchy_lock_active():
            # The countdown keeps running; take over as soon as the user unlocks.
            self.waiting_for_omarchy = True
            return
        self.waiting_for_omarchy = False
        self._close_popup()

        if self.store.get("restore") is None:
            self.store.put("restore", {"paused": system.pause_media(), "stay_awake": system.idle_stay_awake()})
        system.set_idle_stay_awake(True)

        log.info("lock begins: %s (%s) until %s", lock.key, lock.kind, lock.window.end.isoformat())
        if not lock.test:
            self.store.lock_began(lock.key, lock.window.start, lock.window.end, now)
        self.training.begin(now=now, kind=lock.kind, lock_key=lock.key,
                            minutes=(lock.window.end - now).total_seconds() / 60, equipment=self.equipment(),
                            generated=self._agent().fresh_plan(), mode=lock.mode)
        self.current = lock

    def _leave_lock(self, now: datetime) -> None:
        lock = self.current
        self.current = None
        if lock:
            log.info("lock ends: %s (outcome %s)", lock.key, self._outcome(lock.key) or "expired")
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
        if self.locker is not None:
            log.warning("lock screen exited with code %s while a lock is active; relaunching", self.locker.returncode)
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
            "setup": {"profile": self.setup_complete(),
                      "agent_running": bool(self.agent_thread and self.agent_thread.is_alive()),
                      "plan_ready": Agent(self.store, self.training.library, self.config.notebook_id).fresh_plan() is not None},
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

            if cmd == "profile-get":
                return {"ok": True, "profile": profile_mod.load(self.store),
                        "choices": {"experience": profile_mod.EXPERIENCE, "goals": profile_mod.GOALS,
                                    "equipment": profile_mod.EQUIPMENT, "locations": profile_mod.LOCATIONS}}

            if cmd == "profile-save":
                try:
                    saved = profile_mod.save(self.store, request.get("profile") or {}, now)
                except profile_mod.ProfileError as error:
                    return {"ok": False, "error": str(error)}
                self.agent_retry_at = None
                self._schedule_tick()
                return {"ok": True, "profile": saved}

            if cmd == "calendar":
                from . import agenda

                return {"ok": True, "calendar": agenda.build(
                    self.store, self.training.library, self.config, now=now, coach_plan=self._agent().fresh_plan(),
                    lock_plans=self.store.get("lock_plans", {}), policy=self._policy(),
                    days_back=int(request.get("days_back", 14)), days_ahead=int(request.get("days_ahead", 14)))}

            if cmd == "settings-get":
                # Show what's in the file (it may be newer than what's applied, while frozen).
                try:
                    shown = config_mod.load(self.config_path)
                except config_mod.ConfigError:
                    shown = self.config
                return {"ok": True, "settings": config_mod.to_settings(shown), "pending": self.config_pending,
                        "frozen": config_frozen(self._decision(now), now)}

            if cmd == "settings-save":
                try:
                    updated = config_mod.from_settings(request.get("settings") or {}, self.config)
                except config_mod.ConfigError as error:
                    return {"ok": False, "error": str(error)}
                tmp = self.config_path.with_suffix(".tmp")
                tmp.write_text(config_mod.dump(updated))
                tmp.replace(self.config_path)
                self._load_config(now=now)
                log.info("settings saved from the app (%s)", "pending until the lock is over" if self.config_pending else "applied")
                self._schedule_tick()
                return {"ok": True, "pending": self.config_pending}

            if cmd == "agent-status":
                agent = Agent(self.store, self.training.library, self.config.notebook_id)
                return {"ok": True, "plan": agent.fresh_plan(), "stale_plan": self.store.get("next_session"),
                        "runs": self.store.get("agent_runs", []),
                        "running": bool(self.agent_thread and self.agent_thread.is_alive())}

            if cmd == "memory-get":
                from . import agent as agent_mod

                memory = self.store.get(agent_mod.MEMORY_KEY) or {}
                return {"ok": True, "notes": memory.get("notes", []), "updated_at": memory.get("updated_at")}

            if cmd == "memory-forget":
                from . import agent as agent_mod

                if not agent_mod.forget_memory(self.store, int(request.get("id", 0))):
                    return {"ok": False, "error": "no such note"}
                log.info("coach memory: note %s forgotten at the user's request", request.get("id"))
                return {"ok": True}

            if cmd == "doctor":
                issues = diagnostics.check(self.store, self.training.library, lock_active=self.current is not None)
                fixed = diagnostics.repair(issues) if request.get("fix") else 0
                if fixed:
                    log.info("doctor repaired %d issue(s)", fixed)
                return {"ok": True, "issues": [i.as_dict() for i in issues], "fixed": fixed}

            if cmd == "report":
                issues = diagnostics.check(self.store, self.training.library, lock_active=self.current is not None)
                path = diagnostics.build_report(self.store, self.training.library, state=getattr(self, "state", None),
                                                issues=issues, note=str(request.get("note", "")))
                log.info("problem report written: %s", path)
                return {"ok": True, "path": str(path)}

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
        if action == "start_set":
            args["lead_in"] = self.config.lead_in_seconds
        try:
            getattr(self.training, action)(now=now, **args)
        except (TrainingError, TypeError, ValueError) as error:
            log.warning("training action %s %s refused: %s", action, args, error)
            return {"ok": False, "error": str(error)}

        if action == "finish" and (lock := self.current):
            if lock.test:
                self.test_lock = None
            else:
                self.store.lock_ended(lock.key, "completed", now)
                self._notify_progress(now)
        self._schedule_tick()
        return {"ok": True}

    def _notify_progress(self, now: datetime) -> None:
        """After a finished session, say what changes next time."""
        row = self.store.db.execute("SELECT id FROM sessions WHERE status = 'finished' ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return
        changes = self.store.db.execute(
            "SELECT p.rule, p.to_exercise, p.reason FROM proposals p WHERE p.session_id = ? AND p.rule != 'hold'"
            " ORDER BY p.id", (row["id"],)).fetchall()
        arrows = {"up": "↑", "add": "+", "down": "↓", "too-hard": "↓"}
        lines = [f"{arrows.get(c['rule'], '·')} {self.training.library.get(c['to_exercise'])['name']}" for c in changes]
        system.notify("Session done ✓", "Next time: " + ", ".join(lines) if lines else "Same targets next time.")

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
            except Exception:  # keep ticking; a dead service would fail open anyway
                log.exception("tick failed")
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
            log.exception("command failed: %s", locals().get("request"))
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

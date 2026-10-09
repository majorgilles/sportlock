"""The service's JSON-over-Unix-socket API, used by the CLI and the QML screens (`sportlock raw`).

One request per connection: a JSON object with "cmd", answered by {"ok": true, ...} or
{"ok": false, "error": "..."}. The shapes are a contract with the screens; keep them stable.
"""

from __future__ import annotations

import json
import logging
import os
import socketserver
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from sportlock.athlete.domain.profile import EQUIPMENT, EXPERIENCE, GOALS, LOCATIONS
from sportlock.coaching.application.memory_services import ForgetMemoryNoteCommand
from sportlock.diagnostics.infrastructure import doctor, report
from sportlock.exercises.infrastructure.notebooklm_library import NotebookLMLibraryBuilder
from sportlock.locks.application.lock_services import RequestOverrideCommand, StartManualLockCommand
from sportlock.shared_kernel.errors import DomainError
from sportlock.training.application.session_services import RecordTrainingActionCommand

if TYPE_CHECKING:
    from sportlock.app.daemon import Daemon

log = logging.getLogger("sportlock")
TRAIN_ARGS = {"save_set": ("reps", "load_kg"), "rate": ("rpe", "note"), "skip": ("reason",),
              "finish": ("rpe", "notes", "calories", "avg_hr", "body_weight")}


def handle(daemon: Daemon, request: dict) -> dict:
    """Answer one request."""
    with daemon.mutex:
        try:
            return _dispatch(daemon, request)
        except DomainError as error:
            return {"ok": False, "error": str(error)}


def _dispatch(daemon: Daemon, request: dict) -> dict:
    c = daemon.c
    now = c.clock.now()
    cmd = request.get("cmd")
    if cmd == "status":
        return {"ok": True, "state": daemon.state}
    if cmd == "test":
        c.start_test_lock.execute(now)
        daemon.schedule_tick()
        return {"ok": True}
    if cmd == "start":
        c.start_manual_lock.execute(StartManualLockCommand(minutes=int(request.get("minutes", 30))), now)
        daemon.schedule_tick()
        return {"ok": True}
    if cmd == "override":
        c.request_override.execute(RequestOverrideCommand(phrase=str(request.get("phrase", ""))), now)
        daemon.schedule_tick()
        return {"ok": True}
    if cmd == "cancel-override":
        c.cancel_override.execute(now)
        daemon.schedule_tick()
        return {"ok": True}
    if cmd == "train":
        return _train(daemon, request, now)
    if cmd == "profile-get":
        profile = c.profiles.get()
        return {"ok": True, "profile": profile.to_dict() if profile else None,
                "choices": {"experience": EXPERIENCE, "goals": GOALS, "equipment": EQUIPMENT, "locations": LOCATIONS}}
    if cmd == "profile-save":
        profile = c.save_profile.execute(request.get("profile") or {}, now)
        daemon.coach_retry_at = None
        daemon.schedule_tick()
        return {"ok": True, "profile": profile.to_dict()}
    if cmd == "calendar":
        return {"ok": True, "calendar": c.calendar.execute(c.settings.settings, now,
                                                           days_back=int(request.get("days_back", 14)),
                                                           days_ahead=int(request.get("days_ahead", 14)))}
    if cmd == "settings-get":
        return {"ok": True, "settings": c.get_settings.execute().to_form(), "pending": c.settings.pending,
                "frozen": daemon.frozen(now)}
    if cmd == "settings-save":
        pending = c.save_settings.execute(request.get("settings") or {}, decision=c.tick.decision(now), now=now)
        daemon.schedule_tick()
        return {"ok": True, "pending": pending}
    if cmd == "agent-status":
        stored = c.coach_plans.get()
        fresh = c.freshness.fresh_plan()
        return {"ok": True, "plan": fresh.to_dict() if fresh else None, "stale_plan": stored.to_dict() if stored else None,
                "runs": [r.model_dump() for r in c.coach_runs.recent(20)], "running": daemon.coach_running()}
    if cmd == "memory-get":
        notes = c.get_memory.execute()
        return {"ok": True, "notes": [{"id": n.id, "topic": n.topic, "note": n.text, "since": n.since.isoformat()}
                                      for n in notes]}
    if cmd == "memory-history":
        return {"ok": True, "history": c.memory.history(int(request.get("limit", 100)))}
    if cmd == "memory-forget":
        c.forget_memory_note.execute(ForgetMemoryNoteCommand(note_id=int(request.get("id", 0))))
        log.info("coach memory: note %s forgotten at the user's request", request.get("id"))
        return {"ok": True}
    if cmd in ("doctor", "report"):
        status = NotebookLMLibraryBuilder(c.catalogue, c.details.root).status()
        issues = doctor.check(c.database, c.catalogue, c.details, status, lock_active=c.runtime.current is not None)
        if cmd == "report":
            path = report.build_report(c.database, status, state=daemon.state, issues=issues,
                                       note=str(request.get("note", "")))
            log.info("problem report written: %s", path)
            return {"ok": True, "path": str(path)}
        fixed = doctor.repair(issues) if request.get("fix") else 0
        if fixed:
            log.info("doctor repaired %d issue(s)", fixed)
        return {"ok": True, "issues": [i.as_dict() for i in issues], "fixed": fixed}
    if cmd == "log":
        return {"ok": True, "locks": c.lock_events.recent(int(request.get("limit", 20)))}
    if cmd == "reload":
        c.reload_settings.execute(decision=c.tick.decision(now), now=now, force=not c.settings.pending)
        return {"ok": True, "pending": c.settings.pending, "error": c.settings.error}
    return {"ok": False, "error": f"unknown command {cmd!r}"}


def _train(daemon: Daemon, request: dict, now) -> dict:
    c = daemon.c
    action = request.get("action")
    args = {name: request[name] for name in TRAIN_ARGS.get(str(action), ()) if request.get(name) not in (None, "")}
    if action == "start_set":
        args["lead_in"] = c.settings.settings.lead_in_seconds
    try:
        command = RecordTrainingActionCommand(action=action, **args)
    except ValidationError:
        return {"ok": False, "error": f"unknown training action {action!r}" if action not in TRAIN_ACTIONS
                else f"invalid values for {action}"}
    try:
        session, moves = c.record_training_action.execute(command, now)
    except DomainError as error:
        log.warning("training action %s %s refused: %s", action, args, error)
        return {"ok": False, "error": str(error)}
    if action == "finish":
        c.end_lock_on_finish.execute(moves, now)
    daemon.schedule_tick()
    return {"ok": True}


TRAIN_ACTIONS = {"start_set", "stop_set", "end_sets", "swap_easier", "go_now", "cancel_set", "save_set", "rate", "skip",
                 "finish"}


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        """One JSON line in, one JSON line out."""
        try:
            request = json.loads(self.rfile.readline() or b"{}")
            response = handle(self.server.daemon, request)  # type: ignore[attr-defined]
        except Exception as error:
            log.exception("command failed: %s", locals().get("request"))
            response = {"ok": False, "error": repr(error)}
        self.wfile.write(json.dumps(response).encode() + b"\n")


class SocketServer(socketserver.ThreadingUnixStreamServer):
    """Serves the API on a user-only socket."""

    daemon_threads = True

    def __init__(self, daemon: Daemon, path: Path) -> None:
        path.unlink(missing_ok=True)
        super().__init__(str(path), _Handler)
        os.chmod(path, 0o600)
        self.daemon = daemon

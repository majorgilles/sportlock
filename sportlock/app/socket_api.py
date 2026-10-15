"""The service's JSON-over-Unix-socket API, used by the CLI and the QML screens (`sportlock raw`).

One request per connection: a JSON object with "cmd", answered by {"ok": true, ...} or
{"ok": false, "error": "..."}. The shapes are a contract with the screens; keep them stable.
"""

from __future__ import annotations

import json
import logging
import socketserver
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from sportlock.athlete.domain.profile import EQUIPMENT, EXPERIENCE, GOALS, LOCATIONS
from sportlock.coaching.application.memory_services import ForgetMemoryNoteCommand
from sportlock.coaching.application.replacement_services import AnswerReplacementCommand
from sportlock.diagnostics.infrastructure import doctor, report
from sportlock.exercises.infrastructure.notebooklm_library import NotebookLMLibraryBuilder
from sportlock.locks.application.lock_services import RequestOverrideCommand, StartManualLockCommand
from sportlock.music.application.music_services import ControlMusicCommand
from sportlock.shared_kernel.errors import DomainError
from sportlock.training.application.session_services import RecordTrainingActionCommand

if TYPE_CHECKING:
    from sportlock.app.daemon import Daemon

log = logging.getLogger("sportlock")
TRAIN_ARGS = {
    "save_set": ("reps", "load_kg"),
    "rate": ("rpe", "note"),
    "skip": ("reason",),
    "finish": ("rpe", "notes", "calories", "avg_hr", "body_weight"),
}


def handle(daemon: Daemon, request: dict) -> dict:
    """Answer one request."""
    with daemon.mutex:
        try:
            return _dispatch(daemon, request)
        except DomainError as error:
            return {"ok": False, "error": str(error)}


def _status(daemon: Daemon, request: dict, now: datetime) -> dict:
    return {"ok": True, "state": daemon.state}


def _test(daemon: Daemon, request: dict, now: datetime) -> dict:
    daemon.c.start_test_lock.execute(now)
    daemon.schedule_tick()
    return {"ok": True}


def _start(daemon: Daemon, request: dict, now: datetime) -> dict:
    daemon.c.start_manual_lock.execute(StartManualLockCommand(minutes=int(request.get("minutes", 30))), now)
    daemon.schedule_tick()
    return {"ok": True}


def _override(daemon: Daemon, request: dict, now: datetime) -> dict:
    daemon.c.request_override.execute(RequestOverrideCommand(phrase=str(request.get("phrase", ""))), now)
    daemon.schedule_tick()
    return {"ok": True}


def _cancel_override(daemon: Daemon, request: dict, now: datetime) -> dict:
    daemon.c.cancel_override.execute(now)
    daemon.schedule_tick()
    return {"ok": True}


def _trainee_report(daemon: Daemon, request: dict, now: datetime) -> dict:
    return {
        "ok": True,
        "report": daemon.c.report.execute(),
        "writing": daemon.review_running() or daemon.coach_running(),
        "error": daemon.review_error,
    }


def _trainee_report_write(daemon: Daemon, request: dict, now: datetime) -> dict:
    daemon.start_review(now)
    return {"ok": True}


def _replacement_answer(daemon: Daemon, request: dict, now: datetime) -> dict:
    daemon.c.answer_replacement.execute(
        AnswerReplacementCommand(
            chain=str(request.get("chain", "")),
            agree=bool(request.get("agree")),
            note=str(request.get("note", "")),
        ),
        now,
    )
    daemon.schedule_tick()
    return {"ok": True}


def _music(daemon: Daemon, request: dict, now: datetime) -> dict:
    try:
        command = ControlMusicCommand(
            action=request.get("action"), link=str(request.get("link", "")), volume=request.get("volume", 1.0)
        )
    except ValidationError:
        return {"ok": False, "error": f"invalid music request: {request.get('action')!r}"}
    daemon.c.control_music.execute(command)
    daemon.schedule_tick()
    return {"ok": True}


def _music_account(daemon: Daemon, request: dict, now: datetime) -> dict:
    return {
        "ok": True,
        "account": daemon.c.get_music_account.execute().model_dump(),
        "connecting": daemon.music_login_running(),
        "error": daemon.music_login_error,
    }


def _music_login(daemon: Daemon, request: dict, now: datetime) -> dict:
    daemon.start_music_login(str(request.get("client_id", "")))
    return {"ok": True}


def _music_logout(daemon: Daemon, request: dict, now: datetime) -> dict:
    daemon.c.disconnect_music_account.execute()
    return {"ok": True}


def _music_status(daemon: Daemon, request: dict, now: datetime) -> dict:
    return {"ok": True, "music": daemon.c.music_snapshot.execute()}


def _profile_get(daemon: Daemon, request: dict, now: datetime) -> dict:
    profile = daemon.c.profiles.get()
    return {
        "ok": True,
        "profile": profile.to_dict() if profile else None,
        "choices": {"experience": EXPERIENCE, "goals": GOALS, "equipment": EQUIPMENT, "locations": LOCATIONS},
    }


def _profile_save(daemon: Daemon, request: dict, now: datetime) -> dict:
    profile = daemon.c.save_profile.execute(request.get("profile") or {}, now)
    daemon.coach_retry_at = None
    daemon.schedule_tick()
    return {"ok": True, "profile": profile.to_dict()}


def _calendar(daemon: Daemon, request: dict, now: datetime) -> dict:
    c = daemon.c
    return {
        "ok": True,
        "calendar": c.calendar.execute(
            c.settings.settings,
            now,
            days_back=int(request.get("days_back", 14)),
            days_ahead=int(request.get("days_ahead", 14)),
        ),
    }


def _settings_get(daemon: Daemon, request: dict, now: datetime) -> dict:
    c = daemon.c
    return {
        "ok": True,
        "settings": c.get_settings.execute().to_form(),
        "pending": c.settings.pending,
        "frozen": daemon.frozen(now),
    }


def _settings_save(daemon: Daemon, request: dict, now: datetime) -> dict:
    c = daemon.c
    pending = c.save_settings.execute(request.get("settings") or {}, decision=c.schedule_decision.execute(now), now=now)
    daemon.schedule_tick()
    return {"ok": True, "pending": pending}


def _agent_status(daemon: Daemon, request: dict, now: datetime) -> dict:
    c = daemon.c
    stored = c.coach_plans.get()
    fresh = c.freshness.fresh_plan()
    return {
        "ok": True,
        "plan": fresh.to_dict() if fresh else None,
        "stale_plan": stored.to_dict() if stored else None,
        "runs": [r.model_dump() for r in c.coach_runs.recent(20)],
        "running": daemon.coach_running(),
    }


def _memory_get(daemon: Daemon, request: dict, now: datetime) -> dict:
    notes = daemon.c.get_memory.execute()
    return {
        "ok": True,
        "notes": [{"id": n.id, "topic": n.topic, "note": n.text, "since": n.since.isoformat()} for n in notes],
    }


def _memory_history(daemon: Daemon, request: dict, now: datetime) -> dict:
    return {"ok": True, "history": daemon.c.memory.history(int(request.get("limit", 100)))}


def _memory_forget(daemon: Daemon, request: dict, now: datetime) -> dict:
    daemon.c.forget_memory_note.execute(ForgetMemoryNoteCommand(note_id=int(request.get("id", 0))))
    log.info("coach memory: note %s forgotten at the user's request", request.get("id"))
    return {"ok": True}


def _library_status_and_issues(daemon: Daemon) -> tuple[dict, list]:
    c = daemon.c
    status = NotebookLMLibraryBuilder(c.catalogue, c.details.root).status()
    issues = doctor.check(c.database, c.catalogue, c.details, status, lock_active=c.runtime.current is not None)
    return status, issues


def _doctor(daemon: Daemon, request: dict, now: datetime) -> dict:
    _, issues = _library_status_and_issues(daemon)
    fixed = doctor.repair(issues) if request.get("fix") else 0
    if fixed:
        log.info("doctor repaired %d issue(s)", fixed)
    return {"ok": True, "issues": [i.as_dict() for i in issues], "fixed": fixed}


def _report(daemon: Daemon, request: dict, now: datetime) -> dict:
    status, issues = _library_status_and_issues(daemon)
    path = report.build_report(
        daemon.c.database, status, state=daemon.state, issues=issues, note=str(request.get("note", ""))
    )
    log.info("problem report written: %s", path)
    return {"ok": True, "path": str(path)}


def _log(daemon: Daemon, request: dict, now: datetime) -> dict:
    return {"ok": True, "locks": daemon.c.lock_events.recent(int(request.get("limit", 20)))}


def _reload(daemon: Daemon, request: dict, now: datetime) -> dict:
    c = daemon.c
    c.reload_settings.execute(decision=c.schedule_decision.execute(now), now=now, force=not c.settings.pending)
    return {"ok": True, "pending": c.settings.pending, "error": c.settings.error}


def _dispatch(daemon: Daemon, request: dict) -> dict:
    now = daemon.c.clock.now()
    cmd = request.get("cmd")
    handler = HANDLERS.get(cmd) if isinstance(cmd, str) else None
    if handler is None:
        return {"ok": False, "error": f"unknown command {cmd!r}"}
    return handler(daemon, request, now)


def _train(daemon: Daemon, request: dict, now: datetime) -> dict:
    c = daemon.c
    action = request.get("action")
    args = {name: request[name] for name in TRAIN_ARGS.get(str(action), ()) if request.get(name) not in (None, "")}
    if action == "start_set":
        args["lead_in"] = c.settings.settings.lead_in_seconds
    try:
        command = RecordTrainingActionCommand(action=action, **args)
    except ValidationError:
        return {
            "ok": False,
            "error": f"unknown training action {action!r}"
            if action not in TRAIN_ACTIONS
            else f"invalid values for {action}",
        }
    try:
        session, moves = c.record_training_action.execute(command, now)
    except DomainError as error:
        log.warning("training action %s %s refused: %s", action, args, error)
        return {"ok": False, "error": str(error)}
    if action == "finish":
        c.end_lock_on_finish.execute(moves, now)
    daemon.schedule_tick()
    return {"ok": True}


Handler = Callable[["Daemon", dict, datetime], dict]
HANDLERS: dict[str, Handler] = {
    "status": _status,
    "test": _test,
    "start": _start,
    "override": _override,
    "cancel-override": _cancel_override,
    "train": _train,
    "trainee-report": _trainee_report,
    "trainee-report-write": _trainee_report_write,
    "replacement-answer": _replacement_answer,
    "music": _music,
    "music-account": _music_account,
    "music-login": _music_login,
    "music-logout": _music_logout,
    "music-status": _music_status,
    "profile-get": _profile_get,
    "profile-save": _profile_save,
    "calendar": _calendar,
    "settings-get": _settings_get,
    "settings-save": _settings_save,
    "agent-status": _agent_status,
    "memory-get": _memory_get,
    "memory-history": _memory_history,
    "memory-forget": _memory_forget,
    "doctor": _doctor,
    "report": _report,
    "log": _log,
    "reload": _reload,
}


TRAIN_ACTIONS = {
    "start_set",
    "stop_set",
    "end_sets",
    "swap_easier",
    "go_now",
    "cancel_set",
    "save_set",
    "rate",
    "skip",
    "finish",
}


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
        path.chmod(0o600)
        self.daemon = daemon

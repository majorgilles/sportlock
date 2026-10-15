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
from sportlock.coaching.domain.ports import CoachRunFilters, MemoryNoteVersionFilters
from sportlock.diagnostics.infrastructure import doctor, report
from sportlock.exercises.infrastructure.notebooklm_library import NotebookLMLibraryBuilder
from sportlock.locks.application.lock_services import RequestOverrideCommand, StartManualLockCommand
from sportlock.locks.domain.repositories import LockEventFilters
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
    daemon.container.start_test_lock_service.execute(now)
    daemon.schedule_tick()
    return {"ok": True}


def _start(daemon: Daemon, request: dict, now: datetime) -> dict:
    daemon.container.start_manual_lock_service.execute(
        StartManualLockCommand(minutes=int(request.get("minutes", 30))), now
    )
    daemon.schedule_tick()
    return {"ok": True}


def _override(daemon: Daemon, request: dict, now: datetime) -> dict:
    daemon.container.request_override_service.execute(
        RequestOverrideCommand(phrase=str(request.get("phrase", ""))), now
    )
    daemon.schedule_tick()
    return {"ok": True}


def _cancel_override(daemon: Daemon, request: dict, now: datetime) -> dict:
    daemon.container.cancel_override_service.execute(now)
    daemon.schedule_tick()
    return {"ok": True}


def _trainee_report(daemon: Daemon, request: dict, now: datetime) -> dict:
    return {
        "ok": True,
        "report": daemon.container.build_report_service.execute(),
        "writing": daemon.review_running() or daemon.coach_running(),
        "error": daemon.review_error,
    }


def _trainee_report_write(daemon: Daemon, request: dict, now: datetime) -> dict:
    daemon.start_review(now)
    return {"ok": True}


def _replacement_answer(daemon: Daemon, request: dict, now: datetime) -> dict:
    daemon.container.answer_replacement_service.execute(
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
    daemon.container.control_music_service.execute(command)
    daemon.schedule_tick()
    return {"ok": True}


def _music_account(daemon: Daemon, request: dict, now: datetime) -> dict:
    return {
        "ok": True,
        "account": daemon.container.get_music_account_service.execute().model_dump(),
        "connecting": daemon.music_login_running(),
        "error": daemon.music_login_error,
    }


def _music_login(daemon: Daemon, request: dict, now: datetime) -> dict:
    daemon.start_music_login(str(request.get("client_id", "")))
    return {"ok": True}


def _music_logout(daemon: Daemon, request: dict, now: datetime) -> dict:
    daemon.container.disconnect_music_account_service.execute()
    return {"ok": True}


def _music_status(daemon: Daemon, request: dict, now: datetime) -> dict:
    return {"ok": True, "music": daemon.container.music_snapshot_service.execute()}


def _profile_get(daemon: Daemon, request: dict, now: datetime) -> dict:
    profile = daemon.container.profile_repository.get()
    return {
        "ok": True,
        "profile": profile.to_dict() if profile else None,
        "choices": {"experience": EXPERIENCE, "goals": GOALS, "equipment": EQUIPMENT, "locations": LOCATIONS},
    }


def _profile_save(daemon: Daemon, request: dict, now: datetime) -> dict:
    profile = daemon.container.save_profile_service.execute(request.get("profile") or {}, now)
    daemon.coach_retry_at = None
    daemon.schedule_tick()
    return {"ok": True, "profile": profile.to_dict()}


def _calendar(daemon: Daemon, request: dict, now: datetime) -> dict:
    container = daemon.container
    return {
        "ok": True,
        "calendar": container.build_calendar_service.execute(
            container.settings_state.settings,
            now,
            days_back=int(request.get("days_back", 14)),
            days_ahead=int(request.get("days_ahead", 14)),
        ),
    }


def _settings_get(daemon: Daemon, request: dict, now: datetime) -> dict:
    container = daemon.container
    return {
        "ok": True,
        "settings": container.get_settings_service.execute().to_form(),
        "pending": container.settings_state.pending,
        "frozen": daemon.frozen(now),
    }


def _settings_save(daemon: Daemon, request: dict, now: datetime) -> dict:
    container = daemon.container
    pending = container.save_settings_service.execute(
        request.get("settings") or {}, decision=container.get_schedule_decision_service.execute(now), now=now
    )
    daemon.schedule_tick()
    return {"ok": True, "pending": pending}


def _agent_status(daemon: Daemon, request: dict, now: datetime) -> dict:
    container = daemon.container
    stored = container.coach_plan_repository.get()
    fresh = container.coach_plan_freshness.fresh_plan()
    return {
        "ok": True,
        "plan": fresh.to_dict() if fresh else None,
        "stale_plan": stored.to_dict() if stored else None,
        "runs": [r.model_dump(exclude={"id"}) for r in container.coach_run_repository.find(CoachRunFilters(latest=20))],
        "running": daemon.coach_running(),
    }


def _memory_get(daemon: Daemon, request: dict, now: datetime) -> dict:
    notes = daemon.container.get_memory_service.execute()
    return {
        "ok": True,
        "notes": [{"id": n.id, "topic": n.topic, "note": n.text, "since": n.since.isoformat()} for n in notes],
    }


def _memory_history(daemon: Daemon, request: dict, now: datetime) -> dict:
    versions = daemon.container.memory_note_version_repository.find(
        MemoryNoteVersionFilters(latest=int(request.get("limit", 100)), newest_first=True)
    )
    return {"ok": True, "history": [version.model_dump() for version in versions]}


def _memory_forget(daemon: Daemon, request: dict, now: datetime) -> dict:
    daemon.container.forget_memory_note_service.execute(ForgetMemoryNoteCommand(note_id=int(request.get("id", 0))))
    log.info("coach memory: note %s forgotten at the user's request", request.get("id"))
    return {"ok": True}


def _library_status_and_issues(daemon: Daemon) -> tuple[dict, list]:
    container = daemon.container
    status = NotebookLMLibraryBuilder(container.catalogue, container.exercise_details_repository.root).status()
    issues = doctor.check(
        container.database,
        container.catalogue,
        container.exercise_details_repository,
        status,
        lock_active=container.lock_runtime.current is not None,
    )
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
        daemon.container.database, status, state=daemon.state, issues=issues, note=str(request.get("note", ""))
    )
    log.info("problem report written: %s", path)
    return {"ok": True, "path": str(path)}


def _log(daemon: Daemon, request: dict, now: datetime) -> dict:
    events = daemon.container.lock_event_repository.find(
        LockEventFilters(newest_first=True, latest=int(request.get("limit", 20)))
    )
    return {"ok": True, "locks": [event.to_dict() for event in events]}


def _reload(daemon: Daemon, request: dict, now: datetime) -> dict:
    container = daemon.container
    container.reload_settings_service.execute(
        decision=container.get_schedule_decision_service.execute(now),
        now=now,
        force=not container.settings_state.pending,
    )
    return {"ok": True, "pending": container.settings_state.pending, "error": container.settings_state.error}


def _dispatch(daemon: Daemon, request: dict) -> dict:
    now = daemon.container.clock.now()
    cmd = request.get("cmd")
    handler = HANDLERS.get(cmd) if isinstance(cmd, str) else None
    if handler is None:
        return {"ok": False, "error": f"unknown command {cmd!r}"}
    return handler(daemon, request, now)


def _train(daemon: Daemon, request: dict, now: datetime) -> dict:
    container = daemon.container
    action = request.get("action")
    args = {name: request[name] for name in TRAIN_ARGS.get(str(action), ()) if request.get(name) not in (None, "")}
    if action == "start_set":
        args["lead_in"] = container.settings_state.settings.lead_in_seconds
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
        session, moves = container.record_training_action_service.execute(command, now)
    except DomainError as error:
        log.warning("training action %s %s refused: %s", action, args, error)
        return {"ok": False, "error": str(error)}
    if action == "finish":
        container.end_lock_on_session_finished_service.execute(moves, now)
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

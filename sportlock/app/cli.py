"""`sportlock` command line: talks to the running service over its Unix socket (see socket_api.py);
library, ladders and `agent run` work on the data directly."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from datetime import datetime

from sportlock.app.daemon import SOCKET_PATH


def request(payload: dict) -> dict:
    """Send one request to the service and return its answer."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5)
            sock.connect(str(SOCKET_PATH))
            sock.sendall(json.dumps(payload).encode() + b"\n")
            data = b""
            while not data.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
    except OSError:
        sys.exit("sportlock: service not running (systemctl --user start sportlock)")
    return json.loads(data or b"{}")


def _clock(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000).strftime("%a %H:%M")


def _check(response: dict) -> dict:
    if not response.get("ok"):
        sys.exit(f"sportlock: {response.get('error', 'failed')}")
    return response


def cmd_status(args) -> None:
    """`sportlock status`: lock state, next lock, trained today."""
    state = _check(request({"cmd": "status"}))["state"] or {}
    if args.json:
        print(json.dumps(state))
        return
    if state.get("locked"):
        lock = state["lock"]
        minutes = max(0, (lock["end"] - state["updated_at"]) // 60000)
        print(f"locked{' (test)' if lock['test'] else ''}, unlocks by {_clock(lock['end'])} (~{minutes} min)")
        if state.get("override"):
            print(f"override pending, unlocks at {_clock(state['override']['unlock_at'])}")
    elif state.get("waiting_for_omarchy_lock"):
        print("lock due, waiting for the Omarchy lock screen to be unlocked")
    else:
        print("unlocked")
    if state.get("trained_today"):
        print("✓ trained today")
    if state.get("next_lock"):
        nxt = state["next_lock"]
        print(f"next lock: {_clock(nxt['start'])}, {(nxt['end'] - nxt['start']) // 60000} min")
    if state.get("config_error"):
        print(f"config error: {state['config_error']}")
    if state.get("config_pending"):
        print("config change pending until the current lock is over")


def cmd_simple(name):
    """A command that sends one request and prints ok."""
    def run(args) -> None:
        payload = {"cmd": name}
        if name == "override":
            payload["phrase"] = " ".join(args.phrase)
        _check(request(payload))
        print("ok")

    return run


def cmd_start(args) -> None:
    """`sportlock start`: train now."""
    _check(request({"cmd": "start", "minutes": args.minutes}))
    print(f"locked for up to {args.minutes} min, finish the session to unlock earlier")


def cmd_raw(args) -> None:
    """`sportlock raw`: one JSON request, used by the QML screens."""
    print(json.dumps(_check(request(json.loads(args.payload)))))


def cmd_library(args) -> None:
    """`sportlock library …`: build and inspect the exercise library."""
    from sportlock.exercises.infrastructure.details_repository import load_catalogue
    from sportlock.exercises.infrastructure.notebooklm_library import NotebookLMLibraryBuilder
    from sportlock.settings.domain.settings import DEFAULT_NOTEBOOK, SettingsError
    from sportlock.settings.infrastructure.config_toml import TomlSettingsRepository

    try:
        notebook = TomlSettingsRepository().load().notebook_id
    except SettingsError:
        notebook = DEFAULT_NOTEBOOK
    library = NotebookLMLibraryBuilder(load_catalogue(), notebook_id=notebook)

    if args.library_command == "build":
        results = library.build(args.ids or None, force=args.force, workers=args.workers)
        print(f"built {len(results['built'])}, failed {len(results['failed'])}")
        if results["failed"]:
            sys.exit(1)
    elif args.library_command == "pictures":
        results = library.retry_pictures(args.ids or None, workers=args.workers)
        print(f"new pictures {len(results['found'])}, still stick figures {len(results['none'])}, "
              f"failed {len(results['failed'])}")
    elif args.library_command == "infographics":
        results = library.infographics(args.ids or None)
        print(f"installed {len(results['installed'])}, failed {len(results['failed'])}")
    elif args.library_command == "status":
        status = library.status()
        print(f"{status['built']}/{status['total']} exercises built")
        for source, count in sorted(status["pictures"].items()):
            print(f"  pictures from {source}: {count}")
        if status["missing"]:
            print("missing: " + ", ".join(status["missing"]))
    elif args.library_command == "show":
        entry = library.get(args.id)
        print(json.dumps(entry, indent=2, ensure_ascii=False))
    elif args.library_command == "list":
        chain = None
        for exercise_id, spec in library.seed.items():
            if spec["chain"] != chain:
                chain = spec["chain"]
                print(f"\n{chain} ({spec['pattern']})")
            built = "✓" if library.get(exercise_id).get("built_at") else " "
            print(f"  {built} {spec['step']}. {spec['name']}  [{exercise_id}]")


def _target_text(target: dict) -> str:
    work = f"{target['reps'][0]}–{target['reps'][1]} reps" if "reps" in target else f"{target['seconds']} s"
    return f"{target['sets']} × {work}"


def cmd_ladders(args) -> None:
    """`sportlock ladders`: the position on every chain."""
    from sportlock.exercises.infrastructure.details_repository import load_catalogue
    from sportlock.progression.domain.ladders import positions
    from sportlock.progression.infrastructure.sqlite_ladder_repository import SqliteLadderRepository
    from sportlock.shared_kernel.infrastructure.database import Database

    catalogue = load_catalogue()
    for position in positions(SqliteLadderRepository(Database()).saved()):
        exercise = catalogue.find(position.exercise)
        step = f"{exercise.step}/{len(exercise.chain_ids)}" if exercise else "?"
        print(f"{position.chain:<16} {step:>5}  {catalogue.name(position.exercise):<26} "
              f"{_target_text(position.target.to_dict()):<16}{position.reason or 'starting point'}")


def cmd_doctor(args) -> None:
    """`sportlock doctor [--fix]`: consistency checks."""
    response = _check(request({"cmd": "doctor", "fix": args.fix}))
    if not response["issues"]:
        print("No problems found.")
    marks = {"error": "✗", "warning": "!", "info": "·"}
    for issue in response["issues"]:
        fix = f"\n    fix: {issue['fix']}" if issue["fix"] else ""
        print(f"{marks[issue['severity']]} {issue['what']}{fix}")
    if args.fix:
        print(f"\nrepaired {response['fixed']} issue(s)")
    elif any(i["fix"] for i in response["issues"]):
        print("\nrun `sportlock doctor --fix` to repair")


def cmd_report(args) -> None:
    """`sportlock report`: write a problem report."""
    response = _check(request({"cmd": "report", "note": " ".join(args.note)}))
    print(f"report written to {response['path']}")


def cmd_app(args) -> None:
    """`sportlock app`: open the app window (one at a time)."""
    import os
    import subprocess
    from pathlib import Path

    from sportlock.app.daemon import STATE_PATH

    repo = Path(__file__).resolve().parents[2]
    app_dir = str(repo / "sportlock" / "app" / "window")

    # One window at a time: focus the running app instead of opening a second one. Instances
    # without a window (left over from older versions) are closed.
    running = subprocess.run(["pgrep", "-f", f"qs -p {app_dir}$"], capture_output=True, text=True).stdout.split()
    if running:
        clients = subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True).stdout
        with_window = {str(c["pid"]) for c in json.loads(clients or "[]")}
        for pid in running:
            if pid in with_window:
                subprocess.run(["hyprctl", "dispatch", "focuswindow", f"pid:{pid}"], capture_output=True)
                return
            subprocess.run(["kill", pid], capture_output=True)

    env = dict(os.environ, SPORTLOCK_STATE=str(STATE_PATH), SPORTLOCK_BIN=str(repo / "bin" / "sportlock"),
               SPORTLOCK_TAB=args.tab)
    subprocess.Popen(["qs", "-p", app_dir], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def _print_plan(plan: dict) -> None:
    print(f"Coach: {plan.get('rationale', '')}")
    for key in ("hard", "recovery"):
        version = plan[key]
        print(f"\n{key}: {version['title']} ({version['day_type']})")
        for item in version["plan"]:
            print(f"  {item['exercise']:<26} {_target_text(item):<16} {item.get('progress', '')}")


def cmd_agent(args) -> None:
    """`sportlock agent status|run`: the coach's plan and runs."""
    if args.agent_command == "status":
        response = _check(request({"cmd": "agent-status"}))
        if response["running"]:
            print("planning now…")
        if response["plan"]:
            _print_plan(response["plan"])
        elif response["stale_plan"]:
            print("plan is out of date (a session or the profile changed since); a new one is due")
        else:
            print("no plan yet")
        for run in response["runs"][-5:]:
            print(f"{run['at']}  {run['seconds']:>4}s  {'ok' if run['ok'] else 'failed: ' + str(run['error'])}")
        return

    from sportlock.app.container import Container
    from sportlock.app.daemon import Daemon

    daemon = Daemon(Container())
    plan = daemon.c.run_coach.execute(daemon.coach_command(daemon.c.clock.now()))
    _print_plan(plan.to_dict())


def cmd_memory(args) -> None:
    """`sportlock memory [history|forget]`: what the coach remembers."""
    if args.memory_command == "history":
        for row in _check(request({"cmd": "memory-history", "limit": args.limit}))["history"]:
            until = f" → {row['valid_to']} ({row['end_reason']} by {row['ended_by']})" if row["valid_to"] else " (current)"
            print(f"#{row['note_id']:<3} {row['valid_from']}{until}  [{row['topic']}] {row['text']}")
        return
    if args.memory_command == "forget":
        _check(request({"cmd": "memory-forget", "id": args.id}))
        print("forgotten")
        return
    notes = _check(request({"cmd": "memory-get"}))["notes"]
    if not notes:
        print("The coach remembers nothing yet.")
    for note in notes:
        print(f"#{note['id']:<3} {note['since']}  [{note['topic']}] {note['note']}")


def cmd_log(args) -> None:
    """`sportlock log`: recent locks."""
    for lock in _check(request({"cmd": "log", "limit": args.limit}))["locks"]:
        print(f"{lock['start']}  →  {lock['end'][11:]}   {lock['outcome'] or 'active'}")


def cmd_reload(args) -> None:
    """`sportlock reload`: re-read config.toml."""
    response = _check(request({"cmd": "reload"}))
    if response.get("error"):
        sys.exit(f"sportlock: config error: {response['error']}")
    print("pending until the current lock is over" if response.get("pending") else "reloaded")


def main(argv: list[str] | None = None) -> None:
    """Entry point of the `sportlock` command."""
    parser = argparse.ArgumentParser(prog="sportlock")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="show lock state and the next lock")
    status.add_argument("--json", action="store_true")
    status.set_defaults(run=cmd_status)

    sub.add_parser("service", help="run the service (used by systemd)").set_defaults(run=None)
    sub.add_parser("test", help="lock now for 1 minute (no override)").set_defaults(run=cmd_simple("test"))

    override = sub.add_parser("override", help="start the override countdown")
    override.add_argument("phrase", nargs="+")
    override.set_defaults(run=cmd_simple("override"))

    sub.add_parser("cancel-override", help="cancel a pending override").set_defaults(run=cmd_simple("cancel-override"))

    start = sub.add_parser("start", help="start a training session now (locks the desktop)")
    start.add_argument("--minutes", type=int, default=30, choices=(20, 30, 45))
    start.set_defaults(run=cmd_start)

    raw = sub.add_parser("raw", help=argparse.SUPPRESS)  # JSON request, used by the lock screen
    raw.add_argument("payload")
    raw.set_defaults(run=cmd_raw)

    log = sub.add_parser("log", help="recent locks and their outcome")
    log.add_argument("--limit", type=int, default=20)
    log.set_defaults(run=cmd_log)

    sub.add_parser("reload", help="re-read config.toml").set_defaults(run=cmd_reload)

    library = sub.add_parser("library", help="exercise library built from the NotebookLM notebook")
    lib_sub = library.add_subparsers(dest="library_command", required=True)
    build = lib_sub.add_parser("build", help="fill in instructions and pictures (missing exercises only)")
    build.add_argument("ids", nargs="*", help="exercise ids (default: all missing)")
    build.add_argument("--force", action="store_true", help="rebuild even if already built")
    build.add_argument("--workers", type=int, default=4)
    pictures = lib_sub.add_parser("pictures", help="search again for pictures of stick-figure exercises")
    pictures.add_argument("ids", nargs="*", help="exercise ids (default: all with a stick figure)")
    pictures.add_argument("--workers", type=int, default=4)
    info = lib_sub.add_parser("infographics", help="NotebookLM infographics for exercises still drawn as stick figures")
    info.add_argument("ids", nargs="*")
    lib_sub.add_parser("status", help="how much of the library is built")
    show = lib_sub.add_parser("show", help="print one exercise")
    show.add_argument("id")
    lib_sub.add_parser("list", help="all exercises by chain")
    library.set_defaults(run=cmd_library)

    sub.add_parser("ladders", help="where you are on each progression chain").set_defaults(run=cmd_ladders)
    memory = sub.add_parser("memory", help="what the coach remembers about you")
    memory_sub = memory.add_subparsers(dest="memory_command")
    history = memory_sub.add_parser("history", help="every version of every note")
    history.add_argument("--limit", type=int, default=100)
    forget = memory_sub.add_parser("forget", help="remove a note (the coach won't re-add it without new evidence)")
    forget.add_argument("id", type=int)
    memory.set_defaults(run=cmd_memory)
    app = sub.add_parser("app", help="open the sportlock window (profile, schedule & settings)")
    app.add_argument("tab", nargs="?", choices=("calendar", "profile", "settings"), default="calendar")
    app.set_defaults(run=cmd_app)

    doctor = sub.add_parser("doctor", help="check your data for inconsistencies")
    doctor.add_argument("--fix", action="store_true", help="repair what can be repaired")
    doctor.set_defaults(run=cmd_doctor)

    report = sub.add_parser("report", help="write a problem report with logs, state and recent sessions")
    report.add_argument("note", nargs="*", help="what happened, in your words")
    report.set_defaults(run=cmd_report)

    agent = sub.add_parser("agent", help="the coaching agent that plans your next session")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)
    agent_sub.add_parser("status", help="the current plan and recent runs")
    agent_sub.add_parser("run", help="plan the next session now (foreground, for debugging)")
    agent.set_defaults(run=cmd_agent)

    args = parser.parse_args(argv)
    if args.command == "service":
        from sportlock.app.daemon import main as service_main

        service_main()
    else:
        args.run(args)

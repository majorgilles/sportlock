"""`sportlock` command line: talks to the running service over its Unix socket."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from datetime import datetime

from .service import SOCKET_PATH


def request(payload: dict) -> dict:
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
    def run(args) -> None:
        payload = {"cmd": name}
        if name == "override":
            payload["phrase"] = " ".join(args.phrase)
        _check(request(payload))
        print("ok")

    return run


def cmd_start(args) -> None:
    _check(request({"cmd": "start", "minutes": args.minutes}))
    print(f"locked for up to {args.minutes} min, finish the session to unlock earlier")


def cmd_raw(args) -> None:
    print(json.dumps(_check(request(json.loads(args.payload)))))


def cmd_library(args) -> None:
    from . import config as config_mod
    from .library import Library

    try:
        notebook = config_mod.load().notebook_id
    except config_mod.ConfigError:
        notebook = config_mod.DEFAULT_NOTEBOOK
    library = Library(notebook_id=notebook)

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


def cmd_log(args) -> None:
    for lock in _check(request({"cmd": "log", "limit": args.limit}))["locks"]:
        print(f"{lock['start']}  →  {lock['end'][11:]}   {lock['outcome'] or 'active'}")


def cmd_reload(args) -> None:
    response = _check(request({"cmd": "reload"}))
    if response.get("error"):
        sys.exit(f"sportlock: config error: {response['error']}")
    print("pending until the current lock is over" if response.get("pending") else "reloaded")


def main(argv: list[str] | None = None) -> None:
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

    args = parser.parse_args(argv)
    if args.command == "service":
        from .service import main as service_main

        service_main()
    else:
        args.run(args)

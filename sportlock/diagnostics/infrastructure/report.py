"""`sportlock report`: one Markdown file with everything needed to diagnose a problem."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

from sportlock.diagnostics.infrastructure.doctor import Issue
from sportlock.shared_kernel.infrastructure.database import DATA_DIR, Database
from sportlock.shared_kernel.infrastructure.logging_setup import LOG_PATH
from sportlock.training.infrastructure.sqlite_repositories import RUN_KEY

REPORTS_DIR = DATA_DIR / "reports"
REPO_DIR = Path(__file__).resolve().parents[3]


def _cmd(*args: str, timeout: float = 15) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return (result.stdout + result.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"({error})"


def _tail(path: Path, lines: int) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])
    except OSError as error:
        return f"({error})"


def _quickshell_logs(limit: int = 4) -> list[tuple[str, str]]:
    """Recent logs of sportlock's own Quickshell windows (lock screen and app)."""
    base = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "quickshell" / "by-id"
    found = []
    for log in sorted(base.glob("*/log.qslog"), key=lambda p: p.stat().st_mtime, reverse=True):
        text = _cmd("qs", "log", str(log))
        if str(REPO_DIR) in text:
            when = datetime.fromtimestamp(log.stat().st_mtime).isoformat(timespec="seconds")
            found.append((f"{log.parent.name} (last write {when})", "\n".join(text.splitlines()[-60:])))
        if len(found) >= limit:
            break
    return found


def build_report(
    database: Database, library_status: dict, *, state: dict | None, issues: list[Issue], note: str = ""
) -> Path:
    """Write the report; returns its path."""
    now = datetime.now()
    sections = [f"# sportlock problem report — {now.isoformat(timespec='seconds')}"]
    if note:
        sections.append(f"## What happened (user)\n\n{note}")

    sections.append(
        "## Versions\n\n```\n"
        + "\n".join(
            [
                f"sportlock {_cmd('git', '-C', str(REPO_DIR), 'describe', '--always', '--dirty')}",
                f"python {_cmd('/usr/bin/python3', '--version')}",
                _cmd("qs", "--version"),
                f"claude {_cmd('claude', '--version')}",
                _cmd("notebooklm", "--version"),
                f"omarchy {_cmd('cat', '/usr/share/omarchy/version')}",
            ]
        )
        + "\n```"
    )

    sections.append(
        "## Doctor\n\n"
        + (
            "\n".join(f"- **{i.severity}**: {i.what}" + (f" → fix: {i.fix}" if i.fix else "") for i in issues)
            or "No problems found."
        )
    )
    sections.append("## Live state\n\n```json\n" + json.dumps(state, indent=1, ensure_ascii=False)[:20000] + "\n```")

    run = database.get(RUN_KEY)
    sections.append("## Training run (kv)\n\n```json\n" + json.dumps(run, indent=1) + "\n```")
    detail = []
    for s in database.db.execute("SELECT * FROM sessions ORDER BY id DESC LIMIT 3"):
        detail.append(json.dumps({k: s[k] for k in s.keys()}, ensure_ascii=False))
        for e in database.db.execute("SELECT * FROM session_exercises WHERE session_id = ? ORDER BY id", (s["id"],)):
            sets = [
                dict(x)
                for x in database.db.execute(
                    "SELECT set_no, reps, seconds, load_kg, rest_seconds FROM sets"
                    " WHERE session_exercise_id = ? ORDER BY set_no",
                    (e["id"],),
                )
            ]
            detail.append(
                f"  {e['id']} {e['exercise']} [{e['kind']}] {e['status']} rpe={e['rpe']} target={e['target']}"
                f" sets={sets} skip={e['skip_reason']!r}"
            )
    sections.append("## Last 3 sessions\n\n```\n" + "\n".join(detail) + "\n```")
    runs = [dict(r) for r in database.db.execute("SELECT * FROM coach_runs ORDER BY id DESC LIMIT 10")]
    sections.append("## Coach runs\n\n```json\n" + json.dumps(runs, indent=1) + "\n```")
    sections.append("## Library\n\n```json\n" + json.dumps(library_status, indent=1) + "\n```")

    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "sportlock" / "config.toml"
    sections.append("## config.toml\n\n```toml\n" + _tail(config, 200) + "\n```")
    sections.append(
        "## Service\n\n```\n"
        + _cmd("systemctl", "--user", "status", "sportlock", "--no-pager", "-n", "0")
        + "\n```\n\n### Journal (last 80 lines)\n\n```\n"
        + _cmd("journalctl", "--user", "-u", "sportlock", "-n", "80", "--no-pager")
        + "\n```"
    )
    sections.append(f"## sportlock.log (last 200 lines)\n\n```\n{_tail(LOG_PATH, 200)}\n```")
    for name, text in _quickshell_logs():
        sections.append(f"## Quickshell log {name}\n\n```\n{text}\n```")
    sections.append(
        "## Recent core dumps\n\n```\n" + _cmd("coredumpctl", "list", "--since", "-2d", "--no-pager") + "\n```"
    )

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"report-{now.strftime('%Y%m%d-%H%M%S')}.md"
    path.write_text("\n\n".join(sections) + "\n")
    return path

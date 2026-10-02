"""Problem finding and reporting: `sportlock doctor` (consistency checks, optional repair) and
`sportlock report` (one Markdown file with everything needed to diagnose an issue).
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .ladders import START
from .library import Library
from .store import DATA_DIR, Store
from .training import RUN_KEY, convert_target

STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "sportlock"
LOG_PATH = STATE_DIR / "sportlock.log"
REPORTS_DIR = DATA_DIR / "reports"
REPO_DIR = Path(__file__).resolve().parent.parent


def setup_logging() -> logging.Logger:
    """File log for the service (1 MB × 3), plus stderr for the journal."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("sportlock")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        file_handler = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3)
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        logger.addHandler(file_handler)
        logger.addHandler(stream)
    return logger


# -- doctor ------------------------------------------------------------------------------------


@dataclass
class Issue:
    severity: str  # error | warning | info
    what: str
    fix: str | None = None  # description of the repair `--fix` applies, if any
    _apply: object = None

    def as_dict(self) -> dict:
        return {"severity": self.severity, "what": self.what, "fix": self.fix}


def _target_problem(kind: str, target: dict) -> str | None:
    sets = target.get("sets")
    if not isinstance(sets, int) or sets < 1:
        return f"sets is {sets!r}"
    if kind == "reps":
        reps = target.get("reps")
        if not (isinstance(reps, list) and len(reps) == 2 and all(isinstance(r, int) for r in reps) and reps[0] <= reps[1]):
            return f"a reps exercise without a valid rep range ({target})"
    else:
        seconds = target.get("seconds")
        if not isinstance(seconds, (int, float)) or seconds <= 0:
            return f"a {kind} exercise without a duration ({target})"
    return None


def check(store: Store, library: Library, *, lock_active: bool) -> list[Issue]:
    issues: list[Issue] = []
    db = store.db

    # Exercise targets that don't match the exercise's kind (e.g. a swap that kept a rep range).
    for row in db.execute("SELECT id, session_id, exercise, name, kind, target FROM session_exercises"):
        target = json.loads(row["target"])
        problem = _target_problem(row["kind"], target)
        if problem:
            def repair(row=row, target=target):
                db.execute("UPDATE session_exercises SET target = ? WHERE id = ?",
                           (json.dumps(convert_target(target, row["kind"])), row["id"]))
            issues.append(Issue("error", f"session {row['session_id']}: {row['name']} has {problem}",
                                "convert the target to the exercise's kind", repair))

    # Sets that can't be right.
    for row in db.execute(
        "SELECT s.id, s.reps, s.seconds, e.name, e.kind, e.session_id FROM sets s"
        " JOIN session_exercises e ON e.id = s.session_exercise_id"
    ):
        if row["seconds"] is None or row["seconds"] < 0:
            issues.append(Issue("error", f"session {row['session_id']}: a set of {row['name']} has duration {row['seconds']!r}"))
        if row["kind"] == "reps" and row["reps"] is None:
            issues.append(Issue("warning", f"session {row['session_id']}: a set of {row['name']} has no reps "
                                           "(logged while the exercise was set up wrongly)"))

    # Sessions stuck "in progress" with nothing running them.
    run = store.get(RUN_KEY)
    for row in db.execute("SELECT id, started_at FROM sessions WHERE status = 'in_progress'"):
        if run and run.get("session_id") == row["id"]:
            continue
        def repair(row=row):
            db.execute("UPDATE sessions SET status = 'abandoned' WHERE id = ?", (row["id"],))
        issues.append(Issue("warning", f"session {row['id']} (started {row['started_at']}) is still marked in progress",
                            "mark it abandoned", repair))

    # A live training run that points at nothing.
    if run:
        session = db.execute("SELECT status FROM sessions WHERE id = ?", (run.get("session_id"),)).fetchone()
        if session is None or session["status"] != "in_progress":
            if lock_active:
                issues.append(Issue("error", "the running session's record is missing or closed (not repaired during a lock)"))
            else:
                issues.append(Issue("error", "a leftover training run points at a closed or missing session",
                                    "discard the leftover run", lambda: store.delete(RUN_KEY)))

    # Ladders that point at unknown exercises, the wrong chain, or a bad target.
    for row in db.execute("SELECT chain, exercise, target FROM ladders"):
        spec = library.seed.get(row["exercise"])
        problem = None
        if row["chain"] not in START:
            problem = "is not a known chain"
        elif spec is None:
            problem = f"points at unknown exercise {row['exercise']!r}"
        elif spec["chain"] != row["chain"]:
            problem = f"points at {row['exercise']}, which belongs to {spec['chain']}"
        else:
            problem = _target_problem(spec["kind"], json.loads(row["target"]))
        if problem:
            def repair(chain=row["chain"]):
                db.execute("DELETE FROM ladders WHERE chain = ?", (chain,))
            issues.append(Issue("error", f"ladder {row['chain']} {problem}", "reset that chain to its starting point", repair))

    # Lock records left open while no lock is active.
    if not lock_active:
        for row in db.execute("SELECT key FROM lock_events WHERE ended_at IS NULL"):
            def repair(key=row["key"]):
                db.execute("UPDATE lock_events SET ended_at = end, outcome = 'expired' WHERE key = ?", (key,))
            issues.append(Issue("warning", f"lock {row['key']} was never closed (service stopped mid-lock?)",
                                "close it as expired", repair))

    # Library files.
    for exercise_id in library.seed:
        entry = library.get(exercise_id)
        if entry.get("built_at") and entry.get("image") and not Path(entry["image"]).exists():
            issues.append(Issue("warning", f"library: {entry['name']} picture file is missing",
                                None))
    missing = library.status()["missing"]
    if missing:
        issues.append(Issue("info", f"library: {len(missing)} exercises not built yet (run `sportlock library build`)"))

    # Setup and agent.
    if store.get("profile") is None:
        issues.append(Issue("info", "no profile yet: scheduled locks are off until you run `sportlock app`"))
    runs = store.get("agent_runs", [])
    if runs and not runs[-1]["ok"]:
        issues.append(Issue("warning", f"last coach run failed at {runs[-1]['at']}: {runs[-1]['error']}"))
    return issues


def repair(issues: list[Issue]) -> int:
    fixed = 0
    for issue in issues:
        if issue._apply:
            issue._apply()
            fixed += 1
    return fixed


# -- report ------------------------------------------------------------------------------------


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


def build_report(store: Store, library: Library, *, state: dict | None, issues: list[Issue], note: str = "") -> Path:
    now = datetime.now()
    sections = [f"# sportlock problem report — {now.isoformat(timespec='seconds')}"]
    if note:
        sections.append(f"## What happened (user)\n\n{note}")

    sections.append("## Versions\n\n```\n" + "\n".join([
        f"sportlock {_cmd('git', '-C', str(REPO_DIR), 'describe', '--always', '--dirty')}",
        f"python {_cmd('/usr/bin/python3', '--version')}",
        _cmd("qs", "--version"),
        f"claude {_cmd('claude', '--version')}",
        _cmd("notebooklm", "--version"),
        f"omarchy {_cmd('cat', '/usr/share/omarchy/version')}",
    ]) + "\n```")

    sections.append("## Doctor\n\n" + ("\n".join(f"- **{i.severity}**: {i.what}" + (f" → fix: {i.fix}" if i.fix else "")
                                               for i in issues) or "No problems found."))
    sections.append("## Live state\n\n```json\n" + json.dumps(state, indent=1, ensure_ascii=False)[:20000] + "\n```")

    run = store.get(RUN_KEY)
    sections.append("## Training run (kv)\n\n```json\n" + json.dumps(run, indent=1) + "\n```")
    detail = []
    for s in store.db.execute("SELECT * FROM sessions ORDER BY id DESC LIMIT 3"):
        detail.append(json.dumps({k: s[k] for k in s.keys()}, ensure_ascii=False))
        for e in store.db.execute("SELECT * FROM session_exercises WHERE session_id = ? ORDER BY id", (s["id"],)):
            sets = [dict(x) for x in store.db.execute("SELECT set_no, reps, seconds, load_kg, rest_seconds FROM sets"
                                                       " WHERE session_exercise_id = ? ORDER BY set_no", (e["id"],))]
            detail.append(f"  {e['id']} {e['exercise']} [{e['kind']}] {e['status']} rpe={e['rpe']} target={e['target']}"
                          f" sets={sets} skip={e['skip_reason']!r}")
    sections.append("## Last 3 sessions\n\n```\n" + "\n".join(detail) + "\n```")
    sections.append("## Coach runs\n\n```json\n" + json.dumps(store.get("agent_runs", [])[-10:], indent=1) + "\n```")
    sections.append("## Library\n\n```json\n" + json.dumps(library.status(), indent=1) + "\n```")

    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "sportlock" / "config.toml"
    sections.append("## config.toml\n\n```toml\n" + _tail(config, 200) + "\n```")
    sections.append("## Service\n\n```\n" + _cmd("systemctl", "--user", "status", "sportlock", "--no-pager", "-n", "0")
                    + "\n```\n\n### Journal (last 80 lines)\n\n```\n"
                    + _cmd("journalctl", "--user", "-u", "sportlock", "-n", "80", "--no-pager") + "\n```")
    sections.append(f"## sportlock.log (last 200 lines)\n\n```\n{_tail(LOG_PATH, 200)}\n```")
    for name, text in _quickshell_logs():
        sections.append(f"## Quickshell log {name}\n\n```\n{text}\n```")
    sections.append("## Recent core dumps\n\n```\n" + _cmd("coredumpctl", "list", "--since", "-2d", "--no-pager") + "\n```")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"report-{now.strftime('%Y%m%d-%H%M%S')}.md"
    path.write_text("\n\n".join(sections) + "\n")
    return path

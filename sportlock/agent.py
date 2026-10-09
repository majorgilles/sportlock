"""The coaching agent: headless Claude Code writes the next session from the user's history.

Runs after every session (and after onboarding) in the background. It sees the profile, its own
long-term memory of the user (notes it rewrites on every run; see MEMORY_KEY), the
ladder positions, recent sessions in detail, the rule proposals the last session triggered and
the exercise catalogue, and may look things up in the NotebookLM notebook through the
read-only passage search (tools/nlm_search.py) — no other tool. It returns two plans, `hard`
and `recovery`, so the 48-hour rule can still be enforced when the lock actually starts, plus
optional ladder overrides with reasons. Everything is validated before it is stored; on any
failure the local planner is used instead.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from .ladders import START, Ladders
from .library import NLM_SEARCH, Library
from .store import Store, _iso

PLAN_KEY = "next_session"
MEMORY_KEY = "coach_memory"  # {"notes": [{id, topic, note, since}], "forgotten": [note text], "updated_at"}
MEMORY_TOPICS = ("body", "preferences", "progress", "plans", "context", "coaching")
MEMORY_MAX_NOTES = 30
RUNS_KEY = "agent_runs"
HISTORY_DAYS = 28
HISTORY_SESSIONS = 12
TIMEOUT_SECONDS = 900

_ITEM = {
    "type": "object",
    "properties": {
        "exercise": {"type": "string"},
        "sets": {"type": "integer"},
        "reps_low": {"type": ["integer", "null"]},
        "reps_high": {"type": ["integer", "null"]},
        "seconds": {"type": ["integer", "null"]},
        "rest": {"type": "integer"},
        "note": {"type": "string"},
    },
    "required": ["exercise", "sets", "reps_low", "reps_high", "seconds", "rest", "note"],
}
_PLAN = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "exercises": {"type": "array", "items": _ITEM}},
    "required": ["title", "exercises"],
}
SCHEMA = {
    "type": "object",
    "properties": {
        "rationale": {"type": "string"},
        "hard": _PLAN,
        "recovery": {**_PLAN, "properties": {**_PLAN["properties"],
                                             "day_type": {"type": "string", "enum": ["light", "mobility"]}},
                     "required": ["title", "day_type", "exercises"]},
        "next_lock": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["auto", "recovery", "rest"]},
                "recovery_minutes": {"type": ["integer", "null"]},
                "reason": {"type": "string"},
            },
            "required": ["mode", "recovery_minutes", "reason"],
        },
        "memory": {"type": "array", "items": {
            "type": "object",
            "properties": {"topic": {"type": "string", "enum": list(MEMORY_TOPICS)}, "note": {"type": "string"}},
            "required": ["topic", "note"],
        }},
        "recommendations": {"type": "array", "items": {
            "type": "object",
            "properties": {"about": {"type": "string"}, "advice": {"type": "string"}},
            "required": ["about", "advice"],
        }},
        "ladder_overrides": {"type": "array", "items": {
            "type": "object",
            "properties": {"chain": {"type": "string"}, **{k: _ITEM["properties"][k] for k in
                           ("exercise", "sets", "reps_low", "reps_high", "seconds", "rest")},
                           "reason": {"type": "string"}},
            "required": ["chain", "exercise", "sets", "reps_low", "reps_high", "seconds", "rest", "reason"],
        }},
    },
    "required": ["rationale", "memory", "recommendations", "hard", "recovery", "next_lock", "ladder_overrides"],
}

PROMPT = """You are the coach inside "sportlock", a desktop app that locks the user's computer until they
finish a home training session (bodyweight calisthenics and mobility). Write their NEXT session.

Knowledge: the user's own training books are in a NotebookLM notebook. You may search them with
  {search} {notebook} "<query>" --limit 6
(read-only passage search, JSON output). Use it when you need the books' guidance, e.g. on
programming for their level, recovery, or a limitation they mention. Base decisions on the books
and on the data below; don't invent exercises outside the catalogue.

Produce two versions of the next session — the app picks one when the lock starts:
- "hard": used when the last hard session was 48 h or more ago. Normally: warm-up
  (dynamic-warmup), 3–6 main exercises covering different movement patterns, cool-down
  (static-stretch). Build it around the ladder positions, which already include the rule
  engine's adjustments; deviate when the history gives a reason (skips, pain, notes, fatigue,
  repeated grinding, long breaks) and say why in that exercise's note.
  Besides the main patterns, the catalogue has accessory chains you may add when they serve the
  user's goals or limitations: "calf", "hip" (glute medius, hip extension), "core-flexion",
  "conditioning" (cardio intervals, mostly "timed"), and extra warm-up drills ("warmup" chain)
  and stretches ("mobility" chain) to pick from for the warm-up, cool-down and recovery days.
- "recovery": used within 48 h of a hard session. "mobility" (stretching/mobility only) or
  "light" (easy volume on patterns NOT trained hard in the last 48 h, plus mobility).
Plans are trimmed automatically to the lock's length (often 20–30 min), from the end of the main
work, so order exercises by priority. The first and last item are kept.

Each exercise: "exercise" is a catalogue id; "reps"-kind exercises use reps_low/reps_high
(seconds null); "hold" and "timed" use seconds (reps null). For catalogue entries with "sides"
("each": one side then the other; "alternating": switch every rep), reps and seconds count PER
SIDE, so 8 reps means 8 on each side; the app tells the user this, don't restate it in notes. Sets 1–6, rest in seconds. "note" is
shown to the user on the exercise card: one short sentence on why this exercise at this level.

"ladder_overrides": only when you disagree with where the rule engine put a chain for the
future (e.g. it moved them up but notes report pain). Each needs a concrete reason. Usually [].

"next_lock": what the user's NEXT scheduled lock (see "upcoming_locks") should be, given their
recent load ("load"), the time since their last session and how they felt (efforts, notes):
- "auto": the app picks hard if the last hard session was ≥ 48 h before that lock, else recovery;
- "recovery": serve the recovery version, for "recovery_minutes" (5–60) — shorter than the
  scheduled lock when a short mobility block is what they need;
- "rest": skip that lock entirely (a rest day). Only sensible right after a big or hard session
  (e.g. 45+ min, or high effort, the day before). The app enforces limits ("rest_policy"); if
  rest isn't allowed it falls back to recovery.
"reason": one sentence shown to the user (e.g. "Yesterday's 45 min at effort 8 needs a rest day").

"rationale": 1–2 sentences for the user about the overall idea of the next session.

"recommendations": your answer to the feedback from their most recent session (the first entry
of "recent_sessions"): its notes, exercise notes, effort ratings, skips, and how the sets went
against the targets. 1–4 items, each about one thing they said or showed. "about" names it in a
few words (e.g. "Wall push-ups too easy"); "advice" is 1–2 sentences of concrete advice they can
act on: technique, how to make an exercise harder or easier at home, household items to add load,
recovery, or an app setting (e.g. a longer lock in Schedule & settings). Say what changes in the
next session when something does. Use the books where they help. [] when there is nothing to answer.

"memory": your long-term notes about this user, kept between runs. "coach_memory" holds the
current ones; "recent_sessions" only reaches back a few weeks, so anything worth knowing later
must live here. Return the COMPLETE updated list (it replaces the old one): keep what is still
true, update what changed, merge duplicates, drop what is outdated, and add what the latest
session or profile change taught you that will matter beyond the next session. One specific
sentence per note, dated when the date matters (e.g. "2026-10-05: wall push-ups far too easy,
desk-height incline is right"). Topics: "body" (injuries, pain, how the body responds),
"preferences" (likes, dislikes, how they want instructions), "progress" (milestones, what
works), "plans" (what they intend, e.g. longer sessions), "context" (equipment, home setup,
schedule), "coaching" (lessons for you on how to plan for this person). At most {max_notes}.
Never re-add a note listed in "forgotten_by_user" unless there is new evidence for it.

Only use exercises whose equipment the user has. Respect injuries and limitations in the profile.

DATA
{context}
"""


class AgentError(RuntimeError):
    pass


class Agent:
    def __init__(self, store: Store, library: Library, notebook_id: str, *,
                 upcoming_locks: list[dict] | None = None, rest_policy: dict | None = None):
        self.upcoming_locks = upcoming_locks
        self.rest_policy = rest_policy
        self.store = store
        self.library = library
        self.ladders = Ladders(store, library)
        self.notebook_id = notebook_id

    # -- freshness -----------------------------------------------------------------------------

    def last_session_id(self) -> int | None:
        row = self.store.db.execute(
            "SELECT MAX(id) AS id FROM sessions WHERE status != 'in_progress' AND kind NOT IN ('test', 'placeholder')"
        ).fetchone()
        return row["id"]

    def basis(self) -> str:
        """Identifies the data a plan was written from: the latest counted session and the profile."""
        profile = self.store.get("profile") or {}
        return f"session:{self.last_session_id() or 0}/profile:{profile.get('updated_at', '-')}"

    def fresh_plan(self) -> dict | None:
        plan = self.store.get(PLAN_KEY)
        return plan if plan and plan.get("basis") == self.basis() else None

    def needs_run(self) -> bool:
        return self.store.get("profile") is not None and self.fresh_plan() is None

    # -- context -------------------------------------------------------------------------------

    def context(self, now: datetime, equipment: set[str]) -> dict:
        profile = self.store.get("profile") or {}
        ladders = []
        for position in self.ladders.all():
            spec = self.library.get(position["exercise"])
            ladders.append({"chain": position["chain"], "exercise": position["exercise"], "name": spec["name"],
                            "step": f"{spec.get('step')}/{len(spec.get('chain_ids', []))}",
                            "target": position["target"], "why": position["reason"]})

        since = (now - timedelta(days=HISTORY_DAYS)).date().isoformat()
        sessions = []
        for s in self.store.db.execute(
            "SELECT * FROM sessions WHERE day >= ? AND kind NOT IN ('test', 'placeholder') AND status != 'in_progress'"
            " ORDER BY id DESC LIMIT ?", (since, HISTORY_SESSIONS)).fetchall():
            exercises = []
            for e in self.store.db.execute("SELECT * FROM session_exercises WHERE session_id = ? ORDER BY id", (s["id"],)):
                sets = [{k: v for k, v in dict(x).items() if k in ("reps", "seconds", "load_kg", "rest_seconds") and v is not None}
                        for x in self.store.db.execute("SELECT * FROM sets WHERE session_exercise_id = ? ORDER BY set_no", (e["id"],))]
                exercises.append({k: v for k, v in {
                    "exercise": e["exercise"], "status": e["status"], "target": json.loads(e["target"]),
                    "sets": sets, "rpe": e["rpe"], "note": e["note"], "skip_reason": e["skip_reason"]}.items() if v})
            sessions.append({k: v for k, v in {
                "date": s["day"], "started": s["started_at"], "kind": s["kind"], "day_type": s["day_type"],
                "status": s["status"], "rpe": s["rpe"], "notes": s["notes"], "exercises": exercises}.items() if v})

        last = sessions[0] if sessions else None
        proposals = []
        if last:
            row = self.store.db.execute("SELECT MAX(id) AS id FROM sessions WHERE kind NOT IN ('test','placeholder')"
                                        " AND status != 'in_progress'").fetchone()
            proposals = [{"chain": p["chain"], "rule": p["rule"], "from": p["from_exercise"], "to": p["to_exercise"],
                          "target": json.loads(p["to_target"]), "reason": p["reason"]}
                         for p in self.store.db.execute("SELECT * FROM proposals WHERE session_id = ? ORDER BY id", (row["id"],))]

        last_hard = self.ladders.last_hard_session(now)
        from . import recovery

        load = recovery.load_summary(self.store, now)
        catalogue = [{"id": i, "name": spec["name"], "chain": spec["chain"], "step": spec["step"], "kind": spec["kind"],
                      **({"sides": spec["sides"]} if spec.get("sides") else {}),
                      "equipment": spec.get("equipment", []), "available": set(spec.get("equipment", [])) <= equipment}
                     for i, spec in self.library.seed.items()]
        return {
            "now": _iso(now), "profile": {k: v for k, v in profile.items() if k != "updated_at"},
            "coach_memory": [{k: n[k] for k in ("topic", "note", "since")} for n in memory_notes(self.store)],
            "forgotten_by_user": (self.store.get(MEMORY_KEY) or {}).get("forgotten", []),
            "equipment": sorted(equipment),
            "last_hard_session": last_hard and _iso(last_hard),
            "ladders": ladders, "rule_proposals_from_last_session": proposals,
            "load": load, "upcoming_locks": self.upcoming_locks or [], "rest_policy": self.rest_policy or {},
            "recent_sessions": sessions, "catalogue": catalogue,
        }

    # -- running -------------------------------------------------------------------------------

    def run(self, now: datetime, equipment: set[str]) -> dict:
        """Ask the agent, validate, store the plan and apply overrides. Raises AgentError."""
        basis = self.basis()
        started = datetime.now()
        try:
            context = self.context(now, equipment)
            prompt = PROMPT.format(search=NLM_SEARCH, notebook=self.notebook_id, max_notes=MEMORY_MAX_NOTES,
                                   context=json.dumps(context, ensure_ascii=False, indent=1))
            output = self._claude(prompt)
            plan = self.validate(output, equipment)
        except AgentError as error:
            self._log(started, ok=False, error=str(error))
            raise

        plan["basis"] = basis
        plan["generated_at"] = _iso(now)
        plan["feedback_session"] = self.last_session_id() if plan["recommendations"] else None
        self._apply_overrides(plan.pop("overrides"), now)
        memory = plan.pop("memory")
        if memory is not None:
            save_memory(self.store, memory, now)
        self.store.put(PLAN_KEY, plan)
        self._log(started, ok=True, error=None)
        return plan

    def _claude(self, prompt: str) -> dict:
        claude = shutil.which("claude") or str(Path.home() / ".local/bin/claude")
        search = f"Bash({NLM_SEARCH} *)"
        try:
            result = subprocess.run(
                [claude, "-p", prompt, "--output-format", "json", "--no-session-persistence",
                 "--json-schema", json.dumps(SCHEMA), "--tools", "Bash", "--allowedTools", search],
                capture_output=True, text=True, timeout=TIMEOUT_SECONDS, cwd=Path(NLM_SEARCH).parent,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise AgentError(f"could not run Claude: {error}") from None
        try:
            response = json.loads(result.stdout)
        except ValueError:
            raise AgentError(f"Claude failed: {(result.stderr or result.stdout).strip()[-300:]}") from None
        output = response.get("structured_output")
        if response.get("is_error") or not isinstance(output, dict):
            raise AgentError(f"no usable answer: {str(response.get('result'))[:300]}")
        return output

    def _log(self, started: datetime, *, ok: bool, error: str | None) -> None:
        runs = self.store.get(RUNS_KEY, [])
        runs.append({"at": _iso(started), "seconds": round((datetime.now() - started).total_seconds()),
                     "ok": ok, "error": error})
        self.store.put(RUNS_KEY, runs[-20:])

    # -- validation ----------------------------------------------------------------------------

    def validate(self, output: dict, equipment: set[str]) -> dict:
        def items(plan: dict, label: str) -> list[dict]:
            result = []
            for raw in plan.get("exercises", []):
                item = self._item(raw, equipment, label)
                result.append(item)
            if len(result) < 2:
                raise AgentError(f"{label} plan has fewer than 2 exercises")
            return result

        hard = items(output["hard"], "hard")
        recovery = items(output["recovery"], "recovery")
        overrides = []
        for raw in output.get("ladder_overrides", []):
            if raw.get("chain") not in START:
                raise AgentError(f"override for unknown chain {raw.get('chain')!r}")
            item = self._item(raw, equipment, "override")
            if self.library.get(item["exercise"]).get("chain") != raw["chain"]:
                raise AgentError(f"override puts {item['exercise']} on the {raw['chain']} chain")
            if not str(raw.get("reason", "")).strip():
                raise AgentError("override without a reason")
            item.pop("progress", None)
            overrides.append({"chain": raw["chain"], "reason": raw["reason"].strip(), **item})
        next_lock = output.get("next_lock") or {"mode": "auto", "recovery_minutes": None, "reason": ""}
        if next_lock.get("mode") not in ("auto", "recovery", "rest"):
            raise AgentError(f"next_lock mode {next_lock.get('mode')!r}")
        minutes = next_lock.get("recovery_minutes")
        if minutes is not None and (not isinstance(minutes, int) or not 5 <= minutes <= 60):
            raise AgentError(f"next_lock recovery_minutes {minutes!r}")
        memory = None
        if isinstance(output.get("memory"), list):
            memory = [{"topic": raw["topic"], "note": " ".join(str(raw.get("note", "")).split())[:300]}
                      for raw in output["memory"]
                      if isinstance(raw, dict) and raw.get("topic") in MEMORY_TOPICS and str(raw.get("note", "")).strip()]
            memory = memory[:MEMORY_MAX_NOTES]
        recommendations = []
        for raw in output.get("recommendations") or []:
            about, advice = str(raw.get("about", "")).strip(), str(raw.get("advice", "")).strip()
            if advice:
                recommendations.append({"about": about, "advice": advice})
        return {
            "memory": memory,
            "recommendations": recommendations[:4],
            "next_lock": {"mode": next_lock["mode"], "recovery_minutes": minutes,
                          "reason": str(next_lock.get("reason", "")).strip()},
            "rationale": str(output.get("rationale", "")).strip(),
            "hard": {"title": output["hard"]["title"].strip() or "Full body", "day_type": "hard", "plan": hard},
            "recovery": {"title": output["recovery"]["title"].strip() or "Recovery",
                         "day_type": output["recovery"]["day_type"], "plan": recovery},
            "overrides": overrides,
        }

    def _item(self, raw: dict, equipment: set[str], label: str) -> dict:
        exercise = raw.get("exercise")
        if exercise not in self.library.seed:
            raise AgentError(f"{label}: unknown exercise {exercise!r}")
        spec = self.library.get(exercise)
        missing = set(spec.get("equipment", [])) - equipment
        if missing:
            raise AgentError(f"{label}: {exercise} needs {', '.join(sorted(missing))}")
        sets, rest = raw.get("sets"), raw.get("rest")
        if not isinstance(sets, int) or not 1 <= sets <= 6:
            raise AgentError(f"{label}: {exercise} has {sets!r} sets")
        if not isinstance(rest, int) or not 0 <= rest <= 300:
            raise AgentError(f"{label}: {exercise} has rest {rest!r}")
        item = {"exercise": exercise, "sets": sets, "rest": rest}
        if spec["kind"] == "reps":
            lo, hi = raw.get("reps_low"), raw.get("reps_high")
            if not (isinstance(lo, int) and isinstance(hi, int) and 1 <= lo <= hi <= 30):
                raise AgentError(f"{label}: {exercise} has reps {lo!r}–{hi!r}")
            item["reps"] = [lo, hi]
        else:
            seconds = raw.get("seconds")
            if not isinstance(seconds, int) or not 5 <= seconds <= 900:
                raise AgentError(f"{label}: {exercise} has {seconds!r} seconds")
            item["seconds"] = seconds
        if str(raw.get("note", "")).strip():
            item["progress"] = str(raw["note"]).strip()
        return item

    def _apply_overrides(self, overrides: list[dict], now: datetime) -> None:
        for o in overrides:
            target = {k: o[k] for k in ("sets", "reps", "seconds", "rest") if k in o}
            current = self.ladders.get(o["chain"])
            self.store.db.execute(
                "UPDATE proposals SET status = 'overridden', override_reason = ? WHERE id = ("
                " SELECT MAX(id) FROM proposals WHERE chain = ? AND status = 'applied')",
                (o["reason"], o["chain"]),
            )
            self.store.db.execute(
                "INSERT INTO proposals (session_id, chain, rule, from_exercise, from_target, to_exercise, to_target,"
                " reason, status, decided_by, created_at) VALUES ("
                " (SELECT COALESCE(MAX(id), 0) FROM sessions), ?, 'override', ?, ?, ?, ?, ?, 'applied', 'agent', ?)",
                (o["chain"], current["exercise"], json.dumps(current["target"]), o["exercise"], json.dumps(target),
                 o["reason"], _iso(now)),
            )
            self.ladders._set(o["chain"], o["exercise"], target, f"Coach: {o['reason']}", now)


def memory_notes(store: Store) -> list[dict]:
    return (store.get(MEMORY_KEY) or {}).get("notes", [])


def save_memory(store: Store, notes: list[dict], now: datetime) -> None:
    """Replace the coach's notes, keeping each unchanged note's id and the date it was first written."""
    memory = store.get(MEMORY_KEY) or {"notes": [], "forgotten": []}
    old = {(n["topic"], n["note"]): n for n in memory["notes"]}
    next_id = max([n["id"] for n in memory["notes"]], default=0) + 1
    kept = []
    for note in notes:
        previous = old.get((note["topic"], note["note"]))
        if previous:
            kept.append(previous)
        else:
            kept.append({"id": next_id, "topic": note["topic"], "note": note["note"], "since": now.date().isoformat()})
            next_id += 1
    if memory["notes"]:
        store.put(MEMORY_KEY + "_previous", memory)  # one step of undo, should a run go wrong
    store.put(MEMORY_KEY, {"notes": kept, "forgotten": memory.get("forgotten", []), "updated_at": _iso(now)})


def forget_memory(store: Store, note_id: int) -> bool:
    """The user removes a note; the coach is told not to bring it back without new evidence."""
    memory = store.get(MEMORY_KEY) or {"notes": [], "forgotten": []}
    note = next((n for n in memory["notes"] if n["id"] == note_id), None)
    if note is None:
        return False
    memory["notes"] = [n for n in memory["notes"] if n["id"] != note_id]
    memory["forgotten"] = (memory.get("forgotten", []) + [note["note"]])[-30:]
    store.put(MEMORY_KEY, memory)
    return True


def choose(plan: dict, *, last_hard: datetime | None, now: datetime) -> dict:
    """Pick the hard or recovery version by the 48-hour rule."""
    from .ladders import HARD_GAP

    return plan["recovery"] if last_hard and now - last_hard < HARD_GAP else plan["hard"]

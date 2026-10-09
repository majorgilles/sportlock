"""The coach as Claude Code running headless (`claude -p`), with read-only access to the
athlete's books through the NotebookLM passage search, and nothing else."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import override

from sportlock.coaching.domain.memory import MAX_NOTES, TOPICS
from sportlock.coaching.domain.ports import CoachProtocol, CoachUnavailableError

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
        "recovery": {
            **_PLAN,
            "properties": {**_PLAN["properties"], "day_type": {"type": "string", "enum": ["light", "mobility"]}},
            "required": ["title", "day_type", "exercises"],
        },
        "next_lock": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["auto", "recovery", "rest"]},
                "recovery_minutes": {"type": ["integer", "null"]},
                "reason": {"type": "string"},
            },
            "required": ["mode", "recovery_minutes", "reason"],
        },
        "memory": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["add", "update", "delete"]},
                    "id": {"type": ["integer", "null"]},
                    "topic": {"type": ["string", "null"], "enum": [*TOPICS, None]},
                    "note": {"type": ["string", "null"]},
                },
                "required": ["op", "id", "topic", "note"],
            },
        },
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"about": {"type": "string"}, "advice": {"type": "string"}},
                "required": ["about", "advice"],
            },
        },
        "ladder_overrides": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "chain": {"type": "string"},
                    **{
                        k: _ITEM["properties"][k]
                        for k in ("exercise", "sets", "reps_low", "reps_high", "seconds", "rest")
                    },
                    "reason": {"type": "string"},
                },
                "required": ["chain", "exercise", "sets", "reps_low", "reps_high", "seconds", "rest", "reason"],
            },
        },
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

"memory": changes to your long-term notes about this user, kept between runs. "coach_memory"
holds the current notes with their ids; "recent_sessions" only reaches back a few weeks, so
anything worth knowing later must live there. Return only the CHANGES, as operations:
- {{"op": "add", "id": null, "topic": …, "note": …}} for something new the latest session or
  profile change taught you that will matter beyond the next session;
- {{"op": "update", "id": <id>, "topic": …, "note": …}} when a note changed or needs refining
  (merge duplicates by updating one and deleting the other);
- {{"op": "delete", "id": <id>, "topic": null, "note": null}} when a note is no longer true or useful.
[] when nothing changed. One specific sentence per note, dated when the date matters (e.g.
"2026-10-05: wall push-ups far too easy, desk-height incline is right"). Topics: "body"
(injuries, pain, how the body responds), "preferences" (likes, dislikes, how they want
instructions), "progress" (milestones, what works), "plans" (what they intend, e.g. longer
sessions), "context" (equipment, home setup, schedule), "coaching" (lessons for you on how to
plan for this person). At most {max_notes} notes in total. Never re-add a note listed in
"forgotten_by_user" unless there is new evidence for it.

Only use exercises whose equipment the user has. Respect injuries and limitations in the profile.

DATA
{context}
"""


class ClaudeCoach(CoachProtocol):
    """Asks Claude for the plan, forcing its answer into SCHEMA."""

    def __init__(self, notebook_id: str, search_tool: Path) -> None:
        self.notebook_id = notebook_id
        self.search_tool = search_tool

    @override
    def write_plan(self, context: dict) -> dict:
        prompt = PROMPT.format(
            search=self.search_tool,
            notebook=self.notebook_id,
            max_notes=MAX_NOTES,
            context=json.dumps(context, ensure_ascii=False, indent=1),
        )
        claude = shutil.which("claude") or str(Path.home() / ".local/bin/claude")
        try:
            result = subprocess.run(
                [
                    claude,
                    "-p",
                    prompt,
                    "--output-format",
                    "json",
                    "--no-session-persistence",
                    "--json-schema",
                    json.dumps(SCHEMA),
                    "--tools",
                    "Bash",
                    "--allowedTools",
                    f"Bash({self.search_tool} *)",
                ],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                cwd=self.search_tool.parent,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CoachUnavailableError(f"could not run Claude: {error}") from None
        try:
            response = json.loads(result.stdout)
        except ValueError:
            raise CoachUnavailableError(f"Claude failed: {(result.stderr or result.stdout).strip()[-300:]}") from None
        output = response.get("structured_output")
        if response.get("is_error") or not isinstance(output, dict):
            raise CoachUnavailableError(f"no usable answer: {str(response.get('result'))[:300]}")
        return output

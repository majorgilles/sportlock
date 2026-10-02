# sportlock — design

Agreed design (grilling session, 2026-10-02). This is the source of truth for scope.

## Concept

At scheduled times the whole desktop locks into a full-screen **training mode** that serves an
agent-generated session, built from the NotebookLM notebook "Stretching and sport"
(`876228fa-5c2b-4ced-8d81-ee0de4d7e93a`) and the user's history. The lock ends when the session is
**finished or the lock duration runs out, whichever comes first**. It never holds the machine
longer than the lock duration; total lock time is capped per day (default 60 min). Finishing a
session skips the rest of the day's locks.

## Locks

- Schedule: `[[lock]]` entries (days, `at`, `minutes`) in `~/.config/sportlock/config.toml`, also
  editable from the Settings tab. Overlapping entries merge; the daily cap truncates in
  chronological order.
- Config changes made during a lock or its 10-minute warning only apply from the next lock.
- Warnings at T−10 and T−2 min (`omarchy-notification-send`).
- Wall clock: booting or waking mid-window locks for the time left; sleep counts.
- While locked: default sink muted (restored after), Omarchy idle suppressed (restored after).
  If Omarchy's own lock screen is up at lock time, sportlock waits until it is unlocked.
- Override: unlimited; type a phrase, then a 5-minute countdown; every use logged.
- Escape hatch: TTY → `systemctl --user stop sportlock`.
- "Start today's session" (manual, 20/30/45 min) is also a full lock; finishing it skips the
  day's locks.
- Outside workouts can be logged to history but never unlock.
- Locks don't fire until onboarding + library build are complete.

## Training mode

`ext-session-lock` surface on every monitor (Quickshell `WlSessionLock`), Omarchy theme colours.
One exercise card at a time: picture, target, "Details" (steps, cues, mistakes, variations,
citations). Per-set reps/weight with rest timer, one RPE (1–10) per exercise, keyboard driven.
"Too hard" swaps to the easier variation; "Skip" requires a reason. End: overall RPE + notes.
No password unlock.

Pictures: NotebookLM infographic (background, ≤10/day, deleted from Studio after download by
tracked id) → free-exercise-db photo → generated stick figure; user may attach their own.

## Agent

Headless Claude Code (`claude -p`) with the notebooklm skill, restricted to the notebooklm CLI
and sportlock tools; JSON output validated before storing. Generates the next session in the
background after each session. No phases: each session (hard / light / mobility) chosen from
recent history, ≥48 h between hard sessions of the same pattern.

Difficulty: a progression ladder per movement pattern (push, pull, squat, hinge, core, mobility).
Local rules propose (RPE ≤ 6 and all reps → up; RPE ≥ 9 or missed reps → down; else add
reps/sets); the agent may override with a stored reason.

NotebookLM access via `source search` plus a dedicated "sportlock" conversation (never the
user's). A local exercise library is distilled on first run and rebuilt when sources change.
Fallback when no session is ready: build one locally from library + ladders, notify why.

Onboarding form: experience, goals, equipment, injuries, optional age/sex/weight, location.

## App

QML app with tabs Log (filterable, edit/delete, backdating; today's entries frozen during a
lock/warning), Stats (weekly volume, streak, per-exercise RPE trend, ladder positions, day
types), Library, Settings (schedule, cap, override, profile). Bar widget: next lock or
"✓ trained today" + streak. `sportlock export --csv`.

## Implementation

Python stdlib-only user service; SQLite at `~/.local/share/sportlock/` with daily backups (keep
7); unit-tested state machine and rules engine; QML ↔ service via Unix socket (`sportlock` CLI)
and a JSON state file. Repo `~/dev/sportlock`, systemd user unit, Omarchy plugin + menu entry.
`sportlock test` = 1-minute non-overridable lock.

## Milestones

1. Lock + schedule + override with a placeholder session.
2. Training mode + logging.
3. Library + rules engine.
4. Agent.
5. Pictures + stats.

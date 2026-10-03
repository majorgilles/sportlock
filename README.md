# sportlock

Scheduled training locks for [Omarchy](https://omarchy.org). At the times you choose, the whole
desktop locks into a full-screen training session. It unlocks when you **finish the session or
the lock's time runs out, whichever comes first**, so it never holds your computer hostage.

Open the app with `sportlock app` (or Omarchy menu → **Sportlock**): a **Calendar** of past
sessions and planned locks (hard, recovery or rest, with the coach's plan for the next one), your
**Profile**, and **Schedule & settings**.

Sessions are planned by a coach (Claude Code, running headless) from your profile, your history
and the training books in your NotebookLM notebook. Every exercise comes with a picture, step-by-step
instructions and the reason it is at its current difficulty. Everything you do is logged per
set, and difficulty adjusts itself from your reps and effort ratings.

The full agreed design is in [DESIGN.md](DESIGN.md).

---

## Contents

1. [Requirements](#requirements)
2. [Install](#install)
3. [First-time setup](#first-time-setup)
4. [Configuration](#configuration)
5. [During a lock](#during-a-lock)
6. [How sessions are planned](#how-sessions-are-planned)
7. [The exercise library](#the-exercise-library)
8. [Commands](#commands)
9. [Where your data lives](#where-your-data-lives)
10. [If you are ever stuck](#if-you-are-ever-stuck)
11. [Troubleshooting and reporting a problem](#troubleshooting-and-reporting-a-problem)
12. [Uninstall](#uninstall)
13. [Development](#development)

---

## Requirements

- **Omarchy 4** (Hyprland + the Quickshell-based `omarchy-shell`). sportlock uses Quickshell for
  the lock screen and Omarchy's notifications, idle and lock commands.
- **Python 3.11+** at `/usr/bin/python3` (standard library only, no packages).
- **Qt 6 Multimedia** (`qt6-multimedia`) for the ticking clock, and the Qt SVG image plugin.
- **Claude Code** (`claude`) logged in: it plans sessions and builds the exercise library.
- **notebooklm-py** (`notebooklm` CLI, installed with `uv tool install "notebooklm-py[browser]"`)
  logged in with `notebooklm login`, and a NotebookLM notebook with your training books.

## Install

```bash
git clone git@github.com:majorgilles/sportlock.git ~/dev/sportlock
cd ~/dev/sportlock
./install.sh
```

`install.sh` links the `sportlock` command into `~/.local/bin`, installs the systemd **user**
service `sportlock.service` and starts it. The service writes a default config to
`~/.config/sportlock/config.toml` with locks **disabled**.

## First-time setup

Do these once, in order:

1. **Build the exercise library** (about 10 minutes; 48 exercises, 4 in parallel):

   ```bash
   sportlock library build
   sportlock library pictures       # second picture search for any still drawn as stick figures
   sportlock library infographics   # NotebookLM infographics for the rest (max 10 a day)
   sportlock library status         # check: all built, where pictures came from
   ```

2. **Fill in your profile**: `sportlock app`, or Omarchy menu → **Sportlock → Profile &
   settings**. Experience, goals, equipment, injuries or limitations, optional age/sex/weight,
   where you train. Click **Start training** (later: **Save**). You can close the window after
   saving. Scheduled locks stay off until a profile exists; the first save also sets your starting
   level on every exercise chain, and the coach plans your first session within about a minute
   (`sportlock agent status` shows it).

3. **Set your schedule**: `sportlock app settings`, or Omarchy menu → **Sportlock → Schedule &
   settings**. Switch locks **On**, add your lock times (days, start time, minutes), adjust the
   daily cap, warnings, get-ready countdown and override, then **Save**. The page shows your next
   lock. (You can also edit `~/.config/sportlock/config.toml` by hand; see
   [Configuration](#configuration).)

4. **Try it** without waiting for a scheduled lock:

   ```bash
   sportlock test               # 1-minute lock, no override, never counts as training
   sportlock start --minutes 20 # a real session now (20, 30 or 45 min)
   ```

## Configuration

Everything here can be set in `sportlock app settings` (Schedule & settings tab), which writes
this file: `~/.config/sportlock/config.toml`. The service picks up changes by itself (or run
`sportlock reload`). **Changes made during a lock or in the 10 minutes before one only apply once
that lock is over**, so you can't edit your way out of a lock. Sections you leave out use the
defaults shown here.

```toml
[general]
enabled = true              # false: no scheduled locks at all
max_minutes_per_day = 60    # total scheduled lock time per day, all locks combined
warn_minutes = [10, 2]      # notifications before each lock

[training]
lead_in_seconds = 5         # get-ready countdown after pressing Start set (0 = none)

[recovery]
allow_rest_days = true      # the coach may turn a lock into a rest day after a big session
max_rest_days_in_a_row = 2
min_sessions_per_week = 3   # no rest day unless you trained at least this often in the last 7 days
recovery_minutes = 15       # default length of a recovery lock

[override]
phrase = "I am choosing to skip my training today"   # what you type to override a lock
wait_seconds = 300                                   # countdown after typing it

[profile]
# Only used until you save a profile in `sportlock app`, which takes precedence.
equipment = ["chair", "table", "bench", "doorway"]

[notebook]
id = "876228fa-5c2b-4ced-8d81-ee0de4d7e93a"   # NotebookLM notebook with your training books

# One block per scheduled lock. Add as many as you like.
[[lock]]
days = ["mon", "tue", "wed", "thu", "fri"]   # mon … sun
at = "18:00"
minutes = 30

[[lock]]
days = ["sat", "sun"]
at = "10:00"
minutes = 45
```

How locks combine:

- **Overlapping locks merge** into one.
- **The daily cap** (`max_minutes_per_day`) cuts locks short, in time order, once the day's
  total is reached.
- **Finishing a session skips the rest of the day's locks**, whether the session was scheduled or
  started with `sportlock start`.
- **Wall clock**: if the computer was off or asleep when a lock started, it locks on wake/boot for
  the time that is left. Time asleep counts.
- **Warnings** arrive as notifications before each lock.
- **Omarchy's own lock screen**: if it is up when a lock is due, sportlock takes over as soon as
  you unlock it; the countdown keeps running meanwhile.

## During a lock

Every monitor shows the training screen in your Omarchy theme. Media players are paused (and
resumed afterwards) and Omarchy's idle lock is kept off.

**The flow, per exercise:**

1. Read the card: name, target (e.g. *3 sets × 8–12 reps · rest 60 s*), why it is at this level,
   and the picture with cues beside it. **D** shows the full instructions: steps, cues, common
   mistakes, breathing, easier/harder variations and which book they come from.
2. **Start set** (Space). A get-ready countdown (5 s by default, `lead_in_seconds`) ticks down
   while you get into position; **Space** skips it, **Esc** cancels. Then the set clock runs
   with a tick every second (a deeper knock when it starts and every 10 s). The countdown is not
   part of the set's time. Holds and timed blocks ding when you reach the target.
3. **Stop** (Space). The set's duration is recorded automatically. Type the reps (and added
   load in kg if any) and press Enter.
4. A rest countdown starts; it ticks for the last 5 seconds and dings at zero. Start the next set
   whenever you are ready.
5. After the last set, rate the exercise **1–10** (number keys, 0 = 10) and optionally add a note.
   Enter goes to the next exercise.

**Other buttons:** **Too hard** swaps to the easier variation (and lowers that chain for next
time). **Skip…** skips the exercise; a reason is required. **Finish exercise** ends an exercise
early after at least one set.

**At the end:** rate the whole session, write notes (multi-line: Shift+Enter for a new line,
Enter to submit), optionally add calories, average heart rate and body weight from your watch,
then **Finish session**. The screen unlocks and a notification says what changes next time.

**Override:** **Override…** → type the phrase exactly → a 5-minute countdown starts (you can
cancel it). Every override is logged. Test locks can't be overridden.

**Keys:** Space / Enter start and stop a set (and skip the get-ready countdown) · Esc cancel
during the countdown · 1–9, 0 effort · D full instructions · Enter
submits fields · Shift+Enter new line in notes.

## How sessions are planned

**Ladders.** Each exercise chain has a position: the current exercise and its target. The chains
are horizontal push, vertical push, dips, horizontal pull, vertical pull, squat, hinge and core,
each ordered easiest → hardest (e.g. wall push-up → incline → knee → push-up → diamond → archer
→ one-arm). See them with `sportlock ladders` or the whole library with `sportlock library list`.

**Rules** (after every session, `sportlock/rules.py`). For rep exercises:

| Result | Next time |
|---|---|
| Every set at the top of the range, effort ≤ 6 | harder variation |
| Every set at the top of the range, effort 7–8 | +1 rep (at a top of 20: harder variation) |
| Every set at the top of the range, effort 9–10 | same again |
| A set below the range, or fewer sets than planned | easier variation |
| Effort 9–10 without every set at the top | easier variation |
| Otherwise | same again, aim for more reps |
| **Too hard** pressed | easier variation |

Holds (planks etc.) progress in seconds: +10 s when easy, +5 s when moderate, and from 60 s an
easy result moves to the harder variation at 20 s. Warm-up, mobility and cool-down never change.
Test sessions never move ladders.

**The coach.** After every session, and after you save your profile, sportlock runs Claude Code in
the background. It reads your profile (including injuries), ladders, the last few weeks of
sessions (every set, effort, note and skip reason) and the rule changes, and it may search your
NotebookLM books (read-only; it has no other tool). It writes two versions of the next session:

- **hard**, used when your last hard session was 48 h or more ago: warm-up, main work across
  movement patterns, cool-down;
- **recovery** (mobility or light), used within 48 h of a hard session.

Each exercise gets a one-line reason shown on its card, and the session gets a short rationale
shown at the top.

**Recovery and rest days.** Each scheduled lock is decided about 10 minutes before it starts:

- **hard**: the full session for the full lock;
- **recovery**: the recovery version, and the lock is shortened (default 15 min, or what the
  coach chose). Used within 48 h of a hard session, or when the coach asks for it;
- **rest**: no lock at all, with a notification saying why. Only when the coach asks for it
  *and* the guardrails allow it: a counted session in the last 36 h, at least 3 sessions in the
  last 7 days, and no more than 2 rest days in a row (all adjustable in Schedule & settings,
  or switch rest days off). Otherwise it becomes a recovery lock.

The coach decides from your load over the last week (minutes × effort per session), how long ago
you trained, and your efforts and notes. Sessions are sized to the lock from your measured pace:
how long your sets, rests and the gaps between exercises really take.

A session where time runs out after all the main work is done (only the cool-down left) still
counts as a session. The coach may override a rule decision (e.g. hold you back when a note mentions
pain); the reason is stored. Its output is checked (known exercises, your equipment, sane
targets) before it is used. If the coach fails, or its plan is out of date, the built-in planner
builds the session from your ladders, and a notification tells you why. Plans are trimmed to the
lock's length, dropping lower-priority main work first.

## The exercise library

48 exercises, built from your NotebookLM notebook by `sportlock library build`:

- **Instructions** come from passages in your books, found with NotebookLM's passage search. This
  is read-only and never writes into the notebook's chat. Claude turns the passages into steps,
  cues, common mistakes and breathing, and records which books were used.
- **Pictures**, in order of preference: an illustration from your books (Claude looks at every
  candidate and keeps one only if it really shows that exercise), a free-exercise-db photo, a
  NotebookLM infographic, a stick figure. Infographics are limited to 10 a day, and each one is
  deleted from the notebook's Studio panel once downloaded (only the ones sportlock created,
  tracked in `library/infographics.json`).

Rebuild one exercise with `sportlock library build <id> --force` (ids: `sportlock library list`).

## Commands

| Command | What it does |
|---|---|
| `sportlock status` | lock state, next lock, whether you trained today |
| `sportlock start --minutes 20\|30\|45` | start a session now (a full lock) |
| `sportlock test` | 1-minute lock, can't be overridden, doesn't count |
| `sportlock override <phrase>` | start the override countdown (same as the lock-screen button) |
| `sportlock cancel-override` | cancel it |
| `sportlock log` | recent locks and how they ended |
| `sportlock ladders` | your position on every chain, with the reason |
| `sportlock app [calendar\|profile\|settings]` | the app, on the given tab (default: calendar) |
| `sportlock agent status` | the coach's current plan and its recent runs |
| `sportlock agent run` | plan the next session now, in the foreground |
| `sportlock reload` | re-read `config.toml` |
| `sportlock library build [ids] [--force]` | build missing (or given) exercises |
| `sportlock library pictures` | search again for pictures of stick-figure exercises |
| `sportlock library infographics` | NotebookLM infographics for exercises still without a picture |
| `sportlock library status` / `list` / `show <id>` | inspect the library |
| `sportlock doctor [--fix]` | check (and repair) your data |
| `sportlock report "<what happened>"` | write a problem report file |

The Omarchy menu has a **Sportlock** submenu: profile, schedule & settings, and 20/30/45-minute
sessions.

## Where your data lives

| Path | Contents |
|---|---|
| `~/.config/sportlock/config.toml` | schedule and settings |
| `~/.local/share/sportlock/sportlock.db` | SQLite: sessions, exercises, every set, ladders, rule and coach decisions, profile |
| `~/.local/share/sportlock/backups/` | daily database backups (last 7 kept) |
| `~/.local/share/sportlock/library/<id>/` | each exercise's `exercise.json` and picture |
| `~/.local/state/sportlock/sportlock.log` | the service's log |
| `~/.local/share/sportlock/reports/` | problem reports from `sportlock report` |
| `$XDG_RUNTIME_DIR/sportlock/` | live state file and control socket (recreated at boot) |

None of this is in the git repository.

## If you are ever stuck

The lock screen **fails open**: if the service stops, the lock releases within about 15 seconds.

1. Switch to a TTY: **Ctrl+Alt+F3**, and log in.
2. Run `systemctl --user stop sportlock`.
3. Switch back: **Ctrl+Alt+F1** (or F2).

The service starts again at your next login. To skip a lock the honest way, use **Override…**.

## Troubleshooting and reporting a problem

When something looks wrong (odd values on the lock screen, a crash, a session that didn't count):

```bash
sportlock doctor          # checks your data: broken targets, stuck sessions, bad ladders, …
sportlock doctor --fix    # repairs what it can (never touches a lock in progress)
sportlock report "what happened, in your own words"
```

`sportlock report` writes one Markdown file to `~/.local/share/sportlock/reports/` with
everything needed to diagnose the issue: versions, the doctor's findings, the live state, the last
3 sessions with every exercise, target and set, coach runs, library status, your config, the
service journal and log, the lock screen's own logs, and recent core dumps. Run it **right after**
the problem: the lock screen logs live in `$XDG_RUNTIME_DIR` and are gone after a reboot.

The service keeps its own log at `~/.local/state/sportlock/sportlock.log` (lock start/end,
refused actions, coach runs, errors with tracebacks).

Common cases:

- **No lock at the scheduled time.** `sportlock status`: check `enabled = true` (and that you ran
  `sportlock reload`), that a profile exists (`sportlock app`), and whether you already trained
  today. Config errors are shown there too.
- **"Coach couldn't plan your next session."** Check `sportlock agent status`. Usually Claude
  Code isn't logged in, its usage limit was reached, or NotebookLM needs `notebooklm login`. The
  built-in planner is used meanwhile; it retries every 30 minutes.
- **Library build failures.** Rerun `sportlock library build`: it only builds what is missing.
- **No tick sound.** Check that `qt6-multimedia` is installed and your output isn't muted.
- **Service logs:** `journalctl --user -u sportlock -f`.

## Uninstall

```bash
systemctl --user disable --now sportlock
rm ~/.config/systemd/user/sportlock.service ~/.local/bin/sportlock
systemctl --user daemon-reload
# optional: remove your data and config
rm -r ~/.local/share/sportlock ~/.config/sportlock
```

Also remove the `"sportlock…"` lines from `~/.config/omarchy/extensions/omarchy-menu.jsonc`.

## Development

```bash
python3 -m unittest            # all tests, from the repo root
tools/make_sounds.py           # regenerate the tick sounds

# Preview the lock screen in a normal window (no lock) against any state file:
SPORTLOCK_PREVIEW=1 SPORTLOCK_STATE=$XDG_RUNTIME_DIR/sportlock/state.json qs -p locker
```

Layout: `sportlock/` Python service and CLI (`service.py` schedule and locking, `training.py`
session state machine, `rules.py` and `ladders.py` progression, `agent.py` the coach,
`library.py` exercise library, `profile.py` onboarding), `locker/` lock-screen QML, `app/`
profile window QML, `tools/` helpers, `tests/`.

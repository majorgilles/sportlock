# sportlock

Scheduled training locks for Omarchy: at set times the desktop locks into a training session and
unlocks when you finish it or the time runs out. See [DESIGN.md](DESIGN.md) for the full plan.

**Status: milestone 2** — schedule, override, and a training-mode lock screen: one exercise at a
time, Start/Stop per set with a ticking clock (duration measured automatically), reps and load
per set, effort (RPE) per exercise, "Too hard" swaps, skips with a reason, and a session summary.
Sessions come from a fixed starter plan until the agent arrives (milestone 5).

**Milestone 3: exercise library.** Every exercise has a picture and full instructions (steps,
cues, common mistakes, breathing, easier/harder variations, which book it comes from), shown
beside the exercise on the lock screen (press D for the full instructions). They are distilled
from the NotebookLM notebook: passage search (read-only, it never touches the notebook's chat)
returns the passages *and* the book illustrations; headless Claude writes the entry and picks
the illustration that really shows the exercise. Fallbacks, in order: a second search under the
exercise's other names, a free-exercise-db photo, a NotebookLM infographic (at most 10 a day;
each one is deleted from the notebook's Studio panel after download, tracked by id in
`library/infographics.json`), and finally a stick figure.

**Milestone 4: progression.** Every chain (horizontal push, vertical push, dips, horizontal and
vertical pull, squat, hinge, core) has a ladder position: the current exercise and its target.
After each session the rules in `sportlock/rules.py` move it: top of the rep range at effort ≤ 6
→ harder variation; effort 7–8 → +1 rep; below the range, fewer sets, or a grind → easier
variation; "Too hard" → easier straight away. Holds progress in seconds (+10 s / +5 s, harder
variation from 60 s). Every change is stored with its reason and shown on the exercise card.
Sessions are planned from the ladders: a hard full-body day, or a mobility day within 48 h of a
hard one. Set your equipment in `[profile] equipment` (e.g. add "bar" for pull-ups).

## Install

```bash
./install.sh
$EDITOR ~/.config/sportlock/config.toml   # set your schedule, then enabled = true
sportlock reload
```

## Use

```bash
sportlock status            # lock state, next lock, trained today
sportlock start --minutes 30   # start a session now (a full lock: 20, 30 or 45 min)
sportlock test              # lock now for 1 minute (cannot be overridden)
sportlock log               # recent locks and how they ended
sportlock ladders           # where you are on each progression chain
sportlock override <phrase> # start the override countdown (also on the lock screen)
sportlock library build     # build missing exercises (~40 s each, 4 in parallel)
sportlock library build plank --force   # rebuild one exercise
sportlock library pictures  # second picture search for stick-figure exercises
sportlock library infographics   # NotebookLM infographics for any still without a picture
sportlock library status    # how many are built, where pictures came from
sportlock library list      # all progression chains
```

## If you are ever stuck

The locker fails open: stopping the service releases the lock within ~15 seconds.

1. Switch to a TTY with `Ctrl+Alt+F3` and log in.
2. Run `systemctl --user stop sportlock`.
3. Switch back with `Ctrl+Alt+F1` (or F2).

## Develop

```bash
python3 -m unittest        # from the repo root
tools/make_sounds.py       # regenerate the tick sounds

# Preview the lock screen in a normal window (no lock) against any state file:
SPORTLOCK_PREVIEW=1 SPORTLOCK_STATE=$XDG_RUNTIME_DIR/sportlock/state.json qs -p locker
```

Keys on the lock screen: Space/Enter start and stop a set, 1–9 and 0 (=10) pick effort,
D toggles the how-to cues.

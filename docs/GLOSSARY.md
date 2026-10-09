# Glossary

The words the code, the UI and the docs use, with where each lives.

## Locks

**Lock**: the desktop is held by the full-screen training screen until the session is finished or the
lock's time runs out. *Feature:* locks · `locks/domain/locks.py:ActiveLock`

**Window**: a scheduled lock's time span. Its key (start, to the minute, e.g. `2026-10-05T18:00`)
identifies it everywhere. *Feature:* locks · `locks/domain/schedule.py:Window`

**Lock plan**: what a scheduled lock was decided to be, about 10 minutes before it starts: hard,
recovery (maybe shorter) or rest. *Feature:* locks · `locks/domain/locks.py:LockPlan`

**Test lock**: a one-minute lock that can't be overridden and never counts as training.

**Manual lock**: a lock started on request (`sportlock start`). It counts like a scheduled one.

**Override**: ending a lock early by typing the override phrase, after a countdown.
*Feature:* locks · `locks/application/lock_services.py:RequestOverrideService`

**Warning**: a notification before a lock. The first one is also a popup in front of everything.

**Frozen settings**: while a lock is active or due within 10 minutes, settings edits wait until it ends.
*Feature:* locks · `locks/domain/schedule.py:settings_frozen`

## Training

**Session**: one training session on the lock screen, the aggregate the screen drives set by set.
*Feature:* training · `training/domain/session.py:TrainingSession`

**Phase**: where the session is for the current exercise: ready, running, logging, resting, rating,
then summary after the last exercise.

**Lead-in**: the get-ready countdown after pressing Start set. The set's time starts after it.

**Credited session**: a finished session that counts as training. Test, placeholder and outside
sessions never do, nor do abandoned ones (unless time ran out after the main work).

**Fitting**: shrinking a plan to the time a lock leaves, from the measured pace and the gaps between
exercises. *Feature:* training · `training/domain/fitting.py`

**Target**: sets × reps range or seconds, plus rest. Reps and seconds count per side for exercises
with sides. *Shared kernel* · `shared_kernel/targets.py:Target`

**Sides**: *each* means all reps (or the hold) on one side, then the other. *Alternating* means
switching every rep.

## Progression

**Chain**: exercises of one movement pattern, easiest to hardest. *Feature:* exercises ·
`exercises/domain/catalogue.py:Exercise.chain`

**Ladder / position**: where the athlete is on a laddered chain, and the target for next time.
Warm-up, mobility and conditioning are menus, not ladders. *Feature:* progression ·
`progression/domain/ladders.py:LadderPosition`

**Rule / proposal / move**: after a session the rules propose up, add, hold, down or too-hard for
each done exercise. The applied proposal is a move. *Feature:* progression · `progression/domain/rules.py`

**Starting points**: ladder positions above beginner, set once when an experienced athlete first saves
the profile.

**Built-in planner**: the session straight from the ladders, used when the coach has no fresh plan.

## Recovery

**Recovery lock**: a shorter, lighter session within 48 h of a hard one, or when the coach advises it.

**Rest day**: a scheduled lock skipped on the coach's advice, only when the guardrails allow.
*Feature:* recovery · `recovery/domain/policy.py:decide_lock`

**Pace**: real exercise time divided by the naive estimate, measured over recent sessions.

## Coaching

**Coach**: headless Claude Code that writes the next session from everything the app knows.
*Feature:* coaching · `coaching/infrastructure/claude_coach.py`

**Coach plan**: two versions of the next session (hard and recovery), advice for the next lock,
recommendations, ladder overrides. *Feature:* coaching · `coaching/domain/plan.py:CoachPlan`

**Fresh plan**: a plan written from the latest counted session and the current profile (its basis).
A new session makes it stale.

**Recommendations**: the coach's answers to the athlete's feedback on the last session.

**Memory / note**: what the coach remembers across runs, changed by add, update and delete operations
and kept as versions. *Feature:* coaching · `coaching/domain/memory.py:CoachMemory`

**Forgotten note**: a note the athlete removed. The coach must not re-add it without new evidence.

## Exercises

**Catalogue**: every exercise, from the seed data. *Feature:* exercises · `exercises/domain/catalogue.py:Catalogue`

**Library / details**: instructions and pictures built from the athlete's books with NotebookLM.
*Feature:* exercises · `exercises/infrastructure/notebooklm_library.py`

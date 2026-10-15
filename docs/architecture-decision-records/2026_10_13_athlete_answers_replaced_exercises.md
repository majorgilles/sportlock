# The athlete answers replaced exercises before the session

## Status

Accepted (2026-10-13).

## Context

After a session, the progression rules and the coach's overrides can move a ladder to another
exercise (bodyweight squats → step-ups). The athlete only learned about it from the coach's
recommendations, as text, and had no way to say "I'd rather keep squats" before the session
served the new exercise. The coach then had to guess from the next session's notes.

## Decision

- A **replacement** is derived, not stored: a chain whose moves recorded for the last counted
  session start on one exercise while the ladder now holds another. Its reason is that of the
  move that brought the new exercise in (`coaching/domain/replacements.py:replacements_after`).
- The athlete answers in the warning popup (through `sportlock raw`, like the lock screen) and in
  a card at the top of the lock screen while the new exercise is still ahead and not started
  (socket command `replacement-answer`: chain, agree, note).
- Answers are kept in a new `replacement_answers` table. An answered chain is no longer asked.
- **Keep** puts the old exercise back with its old target: a `kept` move decided by the
  `athlete` in `proposals`, the ladder position, the stored coach plan (both versions; its basis
  is unchanged, so it stays fresh) and the running session's exercises that haven't started.
- The coach sees the latest answers as `answers_to_replacements` and is told to respect a "kept".

## Consequences

### Positive

- The athlete decides before training, and the coach learns why.
- No new state to keep in sync: a replacement disappears by itself once the ladder moves back.

### Negative

- Only ladder replacements are asked about; the coach picking different accessory exercises
  between plans is not.
- A session exercise can now change identity in place (`TrainingSession.keep_instead`), so the
  session repository updates its exercise, name, pattern and kind.

## Files

- `sportlock/coaching/domain/replacements.py`, `coaching/application/replacement_services.py`
- `sportlock/coaching/domain/plan.py` (`CoachPlan.keeping`), `training/domain/session.py` (`keep_instead`)
- `sportlock/shared_kernel/infrastructure/database.py` (migration 5)
- `sportlock/locks/ui/warning_popup/shell.qml`, `locks/ui/lock_screen/shell.qml`
- `tests/unit/coaching/test_replaced_exercises.py`, `tests/integration/test_replacements.py`

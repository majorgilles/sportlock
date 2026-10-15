# Repositories are CRUD, and dependencies are named after their type

## Status

`accepted`

**Decision Date:** 2026-10-15

## Context

Repositories had grown their own verbs: `set`, `reset`, `record_move`, `mark_last_overridden` and
`moves_for` on the ladders; `began`, `ended`, `outcome`, `ended_early`, `rest_days` and `recent` on the
lock events; `save_plan`, `set_manual_lock`, `add_warned` and `set_desktop_to_restore` on one key-value
"lock state" port that held four different things; ten read methods on the training history. Each new
need added a new method name, so a reader had to learn every repository separately.

The shared standards in `~/.agents/AGENTS.md` now require both rules below for every project.

## Decision

- Every repository protocol extends the capabilities in `shared_kernel/repositories.py` and declares no
  method of its own: `create`, `get_by_id`, `find(filters)`, `update`, `delete`, and `get` and `update`
  for a single stored value. A single value that can be cleared takes `update(None)`.
- A lookup is an optional field on the entity's `*Filters` value object. A state change is an entity
  method followed by `update`: `event.ended(outcome, now)`, `override.finished(now, cancelled=...)`,
  `record.overridden(reason)`.
- Aggregations are computed from `find` results: `ended_early(events)`, `rest_days(events)`, and the
  `TrainingHistory` query helper (trained days, latest finishes, counts).
- The ports became:

| Before | After |
|---|---|
| `ProfileRepositoryProtocol` get/save | get, update (singleton) |
| `CoachPlanRepositoryProtocol` get/save | get, update (singleton) |
| `CoachRunLogProtocol` add/recent | `CoachRunRepositoryProtocol`: create, find |
| `CoachMemoryRepositoryProtocol` load/save/forgotten/history | get, update (singleton); `MemoryNoteVersionRepositoryProtocol`: find |
| `ReplacementAnswerRepositoryProtocol` add/answered/recent | create, find |
| `CoachReviewRepositoryProtocol` save/get/latest/all | get_by_id, find, create, update |
| `ExerciseDetailsRepositoryProtocol` get | get_by_id |
| `LockEventRepositoryProtocol` began/ended/outcome/ended_early/rest_days/recent/between | create, get_by_id, find, update |
| `OverrideRepositoryProtocol` start/pending/finish | create, find, update |
| `LockStateRepositoryProtocol` (8 methods) | `LockPlanRepositoryProtocol`: create, get_by_id, find; `ManualLockRepositoryProtocol` and `DesktopToRestoreRepositoryProtocol`: get, update (singletons); `GivenWarningRepositoryProtocol`: create, find |
| `LadderRepositoryProtocol` saved/set/reset/record_move/mark_last_overridden/moves_for | `LadderPositionRepositoryProtocol`: find, update, delete; `LadderMoveRecordRepositoryProtocol`: create, find, update |
| `SettingsRepositoryProtocol` marker/load/save | get, update (singleton); `SettingsChangeMarkerProtocol`: marker |
| `TrainingSessionRepositoryProtocol` active/add/save/discard_active | create, update, get (the active session) |
| `TrainingHistoryProtocol` (10 read methods) | find-only `SessionSummary`, `ExerciseTiming`, `TransitionGap`, `CompletedExercise`, `SessionDetails` and `CountedSession` repositories, read by the `TrainingHistory` helper |

- `SessionDetailsRepositoryProtocol` and `CountedSessionRepositoryProtocol` find plain dicts. Those dicts
  are the JSON the coach, the calendar and the report already read, so they stay in that shape.
- The settings file's change marker is not storage. It is its own port, `SettingsChangeMarkerProtocol`.
- No table, column, migration, stored JSON shape, socket answer or state-file field changed.
- A dependency is named after its type: `ladder_position_repository: LadderPositionRepositoryProtocol`.
- `tests/architecture/test_boundaries.py` checks both rules.

## Consequences

### Positive

- Every repository reads the same way, and a new lookup is a new field on a filters value object.
- State changes live on the entities, where the domain tests can reach them.

### Negative

- More, smaller protocols, and a few more queries: the aggregates read whole rows and count in Python.
- Two read models return dicts rather than domain models.

## Files

- `sportlock/shared_kernel/repositories.py`
- `sportlock/*/domain/repositories.py`, `sportlock/*/domain/ports.py`, `sportlock/progression/domain/ladders.py`
- `sportlock/training/application/training_history.py`
- `tests/architecture/test_boundaries.py`

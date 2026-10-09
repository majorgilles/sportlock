# Stable contracts with the QML screens and the stored data

## Status

`accepted`

**Decision Date:** 2026-10-09

## Context

The Python side was rewritten into features and layers. Some things live outside the Python code, and
nothing type-checks them against it:
- the QML screens: the lock screen, the warning popup and the app window;
- the user's database;
- a training run in progress during an upgrade.

## Decision

These shapes are contracts. They are kept as they were, and changed only on purpose in both places:

- **The state file** (`$XDG_RUNTIME_DIR/sportlock/state.json`), written by `app/daemon.py:write_state`.
  The lock screen and the app window read it. The `training` snapshot comes from
  `training/application/snapshot.py`.
- **The socket API** (`app/socket_api.py`), used by `sportlock raw` from the screens and by the CLI:
  - the command names;
  - the request fields;
  - the responses, including the shape of `train`, `calendar`, `settings-get`, `memory-get` and
    `agent-status`.
- **Stored JSON**:
  - targets (`session_exercises.target`, `ladders.target`, `proposals.*_target`);
  - the profile, the coach plan (`kv.next_session`) and lock plans (`kv.lock_plans`);
  - the live run (`kv.training`).
  The domain models read and write these through `from_dict` / `to_dict`.
- **Schema changes** are numbered migrations in `shared_kernel/infrastructure/database.py`: SQL, or a
  function for data moves. They are never edits of earlier steps.

## Consequences

### Positive

- The refactor shipped without touching the QML or migrating stored JSON.
- An upgrade mid-session resumes the session (tested: `test_session__survives_a_restart_of_the_service`).

### Negative

- Some names in these contracts predate the refactor: `agent-status`, `kv.next_session`,
  `waiting_for_omarchy_lock`. Renaming them means changing QML and migrating data, so they stay.

## Files

- `sportlock/app/daemon.py`, `sportlock/app/socket_api.py`
- `sportlock/training/application/snapshot.py`
- `sportlock/locks/ui/lock_screen/shell.qml`, `sportlock/locks/ui/warning_popup/shell.qml`, `sportlock/app/window/shell.qml`

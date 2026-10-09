# Application services: one use case each, `execute`, constructor-injected ports

## Status

`accepted`

**Decision Date:** 2026-10-09

## Context

Use cases used to be methods on one `Service` object, and every one of them could reach every piece
of state. Tests had to build the whole service and patch around it.

## Decision

- **One use case per service class**, named `<Verb><Thing>Service`, with one public method:
  `execute`. Examples: `RunLockTickService`, `RecordTrainingActionService`, `RunCoachService`,
  `ForgetMemoryNoteService`.
- Input is a `*Command` value object, colocated in the service's module, or plain domain values when
  there is nothing to name (`execute(window, settings)`).
- Dependencies are ports (Protocols) and other services, passed to the constructor. There are no
  Protocols *for* application services.
- Business rules live in aggregates and domain functions. The service loads, calls behaviour methods,
  saves, and triggers side effects through ports.
- Wiring happens only in `sportlock/app/container.py`. There is no DI framework.

### Exceptions

- `CoachPlanFreshness` is a small query helper with three methods (`basis`, `fresh_plan`,
  `needs_run`). Several use cases ask it the same questions.
- `SettingsState` and `LockRuntime` are in-memory runtime state shared by services. The settings in
  force and the lock on screen are process state by design: a restart re-decides them.
- The daemon's loop and the background coach thread live in `app/daemon.py`. They orchestrate
  services and own no rules.

## Consequences

### Positive

- Each use case is readable on its own and testable with only what it needs.
- The socket API (`app/socket_api.py`) is a thin map from requests to services.

### Negative

- More classes, and the container lists every service.

## Files

- `sportlock/*/application/*.py`
- `sportlock/app/container.py`
- `sportlock/app/socket_api.py`

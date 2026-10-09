# Feature-first packages, each in clean-architecture layers

## Status

`accepted`

**Decision Date:** 2026-10-09

## Context

sportlock grew as fifteen flat modules. `service.py` (646 lines) scheduled locks, drove the desktop, served
the socket, validated settings and started the coach. `agent.py` held the prompt, the Claude subprocess,
the output validation and the memory storage. The progression rules and the training state machine wrote
SQL themselves. Nothing told a reader what the app does, and a change in one place often needed a
change in three others. The tests mocked `sportlock.service.system.*` and patched private methods to
keep the service from launching windows.

We considered layer-first packages (`domain/`, `application/`, `infrastructure/` at the top) and
started that way. That layout says "this is a DDD app", not "this is an app that locks your desktop
for training". The code for one feature was spread over four trees.

## Decision

**Package by feature first, then by layer inside each feature** (screaming architecture):

```
sportlock/<feature>/domain/          pydantic models, rules, ports (Protocols); no I/O
sportlock/<feature>/application/     use cases; depend on ports only
sportlock/<feature>/infrastructure/  adapters: SQLite, config.toml, Claude, NotebookLM, Quickshell
sportlock/<feature>/ui/              QML screens owned by the feature (locks only)
sportlock/shared_kernel/             base models, Target, SessionPlan, desktop/clock ports, the Database
sportlock/app/                       composition root (container.py), daemon, socket API, CLI, app window
```

Features: athlete, calendar, coaching, diagnostics, exercises, locks, progression, recovery,
settings, training.

### Dependency rules

- The domain imports only the standard library, pydantic, the shared kernel, its own feature, and
  the features listed in the **context map**:
  - coaching → exercises (validating plans needs the catalogue)
  - locks → settings (the schedule is part of the settings)
  - progression → exercises (ladders walk the chains)
  - recovery → training (it decides from training history)
  - training → exercises (a session is built from catalogue entries)
- Application services may use other features' domains and application services. They never import
  infrastructure or `app`.
- Adapters never import application or `app`.
- Only `app` imports `app`. `app/container.py` is the one module that knows every concrete class.
- The shared kernel depends on no feature.

`tests/architecture/test_boundaries.py` checks all of this on every module's imports, using the AST. A
new cross-feature domain dependency is added to the context map in that test, with a reason here.

### Exceptions

- `diagnostics` is infrastructure only. Its checks are about how the data is stored (stuck runs,
  targets that don't match the exercise kind), so a domain model would only mirror the tables.
- `exercises/infrastructure/notebooklm_library.py` is a build tool. The CLI calls it directly; there
  is no use case around it.

## Consequences

### Positive

- The tree reads like the app's feature list, and one feature's change stays in one folder.
- Business rules are plain models and functions, tested without a database (109 unit tests in 0.1 s).
- Adapters can be swapped in one place, the container. Tests pass fakes for the desktop, the screens,
  the coach and the clock there.

### Negative

- More files and more imports than the flat layout, for a small app.
- Some read models (session history for the coach and the calendar) are dicts, not models, to keep
  the queries simple.

## Files

- `tests/architecture/test_boundaries.py`: the dependency rules
- `sportlock/app/container.py`: composition root
- `CLAUDE.md`: the rules in short, for contributors and Claude

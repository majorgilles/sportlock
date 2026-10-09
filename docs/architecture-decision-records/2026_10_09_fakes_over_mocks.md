# Fakes for the outside world, real SQLite for repositories, no mocks

## Status

`accepted`

**Decision Date:** 2026-10-09

## Context

The old tests patched `sportlock.service.system.*`, `Service._ensure_locker`, `Service._show_popup` and
`Agent._claude` with `mock.patch`. Renaming a private method broke tests without changing behaviour,
and a patched path that no longer existed passed silently.

## Decision

- **No `mock.patch`, no `Mock(spec=...)`.**
- The outside world is faked through its ports, in `tests/world.py`:
  - `FakeDesktop` records notifications and keeps media and idle state as attributes;
  - `FakeLockScreen` and `FakePopup` record what they were asked to show;
  - `FakeCoach` returns a prepared answer and records each context it was given;
  - `FakeClock` is set by the test.
- **Repositories are the real SQLite adapters on a temporary file.** Each test's `World` builds
  the full app through `Container`. In-memory fake repositories would duplicate the SQL's
  behaviour and drift from it, and SQLite in a temp file runs the whole suite in under a second.
- Layout:
  - `tests/unit/<feature>/`: domain models and rules, no database;
  - `tests/integration/`: use cases end to end through the socket API and the container;
  - `tests/architecture/`: the dependency rules.
- Style follows lizy-backend:
  - flat `test_*` functions, named `test_<function>__<case>__<expected>`;
  - `# given / # when / # then` where the phases aren't obvious;
  - hard-coded expected values;
  - one behaviour per test.

## Consequences

### Positive

- Tests check behaviour (state, notifications, rows), not calls, and survive refactors.
- The integration tests also cover the SQL and the migrations.

### Negative

- Integration tests need the real catalogue and a temp directory. A `World` costs a few milliseconds.

## Files

- `tests/world.py`: fakes and `World`
- `tests/conftest.py`: sandboxed XDG directories, `catalogue` and `world` fixtures

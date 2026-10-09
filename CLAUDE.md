# sportlock

Scheduled training locks for Omarchy. Read README.md for what the app does, docs/GLOSSARY.md for the
words, and docs/architecture-decision-records/ for why it is built this way.

## Commands

```bash
uv sync                  # environment (pydantic, pytest, ruff)
uv run ruff format       # format; run before every commit
uv run ruff check --fix  # lint (pyflakes, import order)
uv run pytest            # all tests; must pass before every commit
systemctl --user restart sportlock   # load code changes into the running service (only while unlocked)
```

## Architecture (enforced by tests/architecture/test_boundaries.py)

- **Package by feature, then by layer**: `sportlock/<feature>/{domain,application,infrastructure}`.
  Shared building blocks are in `sportlock/shared_kernel/`. Wiring and entry points are in
  `sportlock/app/` (container, daemon, socket API, CLI, app window).
- **Domain**: pydantic models from `shared_kernel/base.py` (`ValueObject`, `Entity`, `Aggregate`,
  `DomainEvent`), with no `@dataclass`. No I/O. Imports only the standard library, pydantic, the
  shared kernel, its own feature and the features in the context map
  (`2026_10_09_feature_first_clean_architecture.md`). Ports are `typing.Protocol`, named `*Protocol`.
- **Business rules belong in aggregates and domain functions.** Change aggregate state through
  behaviour methods (`session.save_set(...)`), not field assignment from outside.
- **Application**: one use case per `*Service` with a single `execute`. Inputs are `*Command` value
  objects in the same module. Dependencies are ports, passed to the constructor. Never import
  infrastructure or `app`.
- **Infrastructure**: adapters subclass their port and mark methods `@override`. They never import
  application or `app`.
- **Only `app/container.py` builds concrete classes.**
- Absolute imports (`from sportlock.locks.domain.schedule import ...`), never relative.
- Contracts: the state file, the socket API and stored JSON shapes are read by the QML screens and
  live data. Keep them stable (`2026_10_09_stable_contracts_with_the_screens.md`). Schema changes are
  new numbered migrations.

## Tests

- No `mock.patch` and no `Mock(spec=...)`. Fake the outside world through ports (`tests/world.py`).
  Repositories are the real SQLite adapters on a temp file (`2026_10_09_fakes_over_mocks.md`).
- `tests/unit/<feature>/` for domain rules, `tests/integration/` for use cases through `World`.
- Flat functions named `test_<function>__<case>__<expected>`, with `# given / # when / # then` when
  the phases aren't obvious, hard-coded expected values, and one behaviour per test.

## Docs

- Every module, class and public method has a one-line docstring.
- A decision that shapes more than one place gets an ADR. Name the file
  `docs/architecture-decision-records/YYYY_MM_DD_snake_case_title.md`, with sections Status, Context,
  Decision, Consequences (Positive / Negative) and Files.

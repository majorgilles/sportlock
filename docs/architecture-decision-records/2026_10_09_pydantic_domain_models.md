# Pydantic for domain models, in a uv-managed environment

## Status

`accepted`

**Decision Date:** 2026-10-09

## Context

Until now sportlock was standard-library only. It ran on `/usr/bin/python3` with no installed packages,
and passed dicts around for targets, plans and profiles. Validation was hand-written at each edge,
for example the coach's output and the profile form. Typos in dict keys were found at runtime.

The refactor to domain models needed a way to declare value objects, entities and aggregates. Frozen
dataclasses would have kept the no-dependency rule. Pydantic gives validation on construction and
assignment, JSON round-trips (`model_dump`, `model_validate`), and the same building blocks as our
other projects.

## Decision

**Domain models are pydantic models**, from `shared_kernel/base.py`:

- `ValueObject`: frozen, `extra="forbid"`. Examples: `Target`, `Window`, `MemoryNote`.
- `Entity`: `validate_assignment=True`. Examples: `SessionExercise`, `LoggedSet`.
- `Aggregate(Entity)`: records `DomainEvent`s through `record()`. The application or the repository
  takes them with `pull_events()`. Examples: `TrainingSession`, `CoachMemory`.
- Commands for use cases are `ValueObject`s named `*Command`.

No `@dataclass` in the domain.

The project gets a `pyproject.toml` and a uv-managed `.venv`. `bin/sportlock` re-executes itself with
`.venv/bin/python`, so the systemd unit, the CLI and the QML screens (which call `bin/sportlock`) all
run inside the environment. `install.sh` runs `uv sync`. Test tooling (pytest) is a dev dependency
group.

The stored JSON shapes did not change. `Target.to_dict`/`from_dict`, `SessionPlan`, `CoachPlan` and
`Profile` read and write exactly what earlier versions stored.

## Consequences

### Positive

- Invalid states fail where they are created, with a clear error.
- Models document themselves; the coach's plan is a typed `CoachPlan` instead of a nested dict.

### Negative

- The app now needs uv and a network fetch at install time.
- Pydantic models take keyword arguments only, and `model_copy(update=...)` skips validation. Use
  `Target.with_(...)` when a changed value must be validated.

## References

- `2026_10_09_feature_first_clean_architecture.md`

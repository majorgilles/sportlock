# Coach memory: versioned notes changed by explicit operations

## Status

`accepted`

**Decision Date:** 2026-10-09

## Context

The coach (headless Claude Code) plans each session from the profile, the ladders and the last 4 weeks
or 12 sessions. Anything older, or anything it inferred but did not write into an exercise note, was
lost. A first version kept up to 30 notes in one key-value JSON value. The coach returned the complete
list each run and it replaced the old one, with a single previous copy kept for undo. That design
had two problems. A bad run could silently drop notes. And there was no history of how the coach's
picture of the athlete changed.

We looked at memory libraries:

- **LangMem**: needs an API key for its LLM calls, plus an embedding provider for search. sportlock
  runs the coach through the user's Claude Code login and has no API key. Rejected on that condition.
- **mem0**: LLM-driven extraction and a vector store, with OpenAI as the default embedding provider.
  Same API-key problem, and a large dependency for about 30 notes.
- **Letta (MemGPT)**: a full agent server that would replace the coach, not help it.
- **Zep / Graphiti**: facts with validity intervals (a good idea), but it needs a graph database server.
- **sqlite-vec**: only useful once the memory is too big to send whole. At 30 notes it isn't.

All of these mostly solve retrieval among thousands of memories. Our coach reads every note every time.

## Decision

**Notes are an aggregate (`CoachMemory`) changed by explicit operations, and persisted as versions.**

- The coach's answer has a `memory` list of operations: `add` (topic and note), `update` (id, plus a
  new topic and/or note), `delete` (id). `[]` means nothing changed. It never rewrites the list. This
  borrows LangMem's insert/update/delete manager idea without the library.
- `CoachMemory.apply` validates each operation. Unknown ids, empty notes and adds past 30 notes are
  refused one by one, and the rest still apply. One bad operation never loses the plan.
- Each change is a domain event (`NoteAdded`, `NoteUpdated`, `NoteDeleted`, `NoteForgotten`).
  `SqliteCoachMemoryRepository.save` turns events into rows of `coach_memory_notes`:
  - each version of a note gets a row with `valid_from` / `valid_to`, like Graphiti's validity intervals;
  - `written_by` / `ended_by` record the coach run (`coach-run:<id>`) or `user`;
  - `end_reason` is `updated`, `deleted` or `forgotten`.
- The current notes are the rows with no `valid_to`. History is every row
  (`sportlock memory history`).
- When the athlete removes a note (`NoteForgotten`), its text is shown to the coach as
  `forgotten_by_user`. The coach is told not to re-add it without new evidence.
- Coach runs moved from a key-value list to a `coach_runs` table, so a note can name the run that
  wrote it.

## Consequences

### Positive

- No silent loss: a note disappears only through a recorded `delete` or `forgotten`.
- Full history, and "what did the coach believe on date X" is a query.
- No dependency, no API key, and it works offline apart from the coach itself.

### Negative

- The coach must send ids, so its prompt is a little longer and an id it makes up is refused.
- There is no undo command yet. A wrong run can be reverted by hand from the history rows.

## Files

- `sportlock/coaching/domain/memory.py`: aggregate, operations, events
- `sportlock/coaching/infrastructure/sqlite_repositories.py`: `SqliteCoachMemoryRepository`
- `sportlock/coaching/infrastructure/claude_coach.py`: the prompt and the JSON schema
- `sportlock/shared_kernel/infrastructure/database.py`: migrations 3 and 4

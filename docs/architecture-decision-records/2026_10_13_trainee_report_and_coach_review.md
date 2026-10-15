# The trainee report and the coach's review

## Status

Accepted (2026-10-13).

## Context

The athlete wanted a report about themselves as a trainee: a dossier, their last session, a
journal, their stats, and where they can improve. The app had the raw material (sessions, sets,
notes, ladder moves, the coach's memory) but no place to read it. The coach's answers to the
feedback lived only in the latest plan and were lost at the next run.

## Decision

- A new **reporting** feature builds the Report tab. Its domain only counts (stats, personal
  bests, ladder levels, plain observations) from value objects the application fills from the
  training history, the ladders and the lock events. These parts are always current.
- The words come from a **coach review**, a second, smaller coach call (`CoachProtocol.write_review`,
  its own prompt and schema): the dossier, a word on the last session, and up to four improvements.
  The coach sees the planning context plus the report's stats and ladder levels.
- The review runs in the background right after each successful plan, and on demand from the tab
  (socket `trainee-report-write`). A failed review only logs; planning is not affected.
- Reviews are kept per session in a new `coach_reviews` table, together with the plan's answers
  to that session's feedback, so the journal shows every past answer. The migration seeds the
  table with the current plan's answers.
- The report is served by the socket command `trainee-report` (`report` already sends problem
  reports).

## Consequences

### Positive

- The athlete sees their progress, history and advice in one place, and the coach's past answers
  are no longer overwritten.
- The numbers don't depend on the coach: the tab works before any review and when Claude is down.

### Negative

- Two coach calls per session instead of one (the review is short, and runs after the plan).
- Sessions before this change have no coach review in the journal, except the last one's answers.

## Files

- `sportlock/reporting/domain/report.py`, `sportlock/reporting/application/report_services.py`
- `sportlock/coaching/domain/review.py`, `coaching/infrastructure/claude_coach.py` (`REVIEW_PROMPT`)
- `sportlock/coaching/infrastructure/sqlite_repositories.py` (`SqliteCoachReviewRepository`)
- `sportlock/shared_kernel/infrastructure/database.py` (migration 6)
- `sportlock/app/window/shell.qml` (Report tab), `app/daemon.py`, `app/socket_api.py`
- `tests/unit/reporting/test_stats.py`, `tests/integration/test_trainee_report.py`

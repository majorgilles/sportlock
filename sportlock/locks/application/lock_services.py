"""Use cases of locks.

The tick that locks and unlocks the desktop, and the athlete's requests (test lock, session now, override).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sportlock.coaching.application.fresh_plan import CoachPlanFreshness
from sportlock.exercises.domain.catalogue import Catalogue
from sportlock.locks.domain.locks import ActiveLock, LockOutcome, LockPlan, Override, rest_days
from sportlock.locks.domain.repositories import (
    LockEventFilters,
    LockEventRepositoryProtocol,
    LockPlanRepositoryProtocol,
    LockRuntimeProtocol,
    ManualLockRepositoryProtocol,
    OverrideFilters,
    OverrideRepositoryProtocol,
)
from sportlock.locks.domain.schedule import Window
from sportlock.progression.domain.ladders import LadderMove
from sportlock.recovery.domain.policy import CoachAdvice, Policy, RecoveryState, decide_lock, rest_days_in_a_row
from sportlock.settings.application.settings_services import SettingsState
from sportlock.settings.domain.settings import Settings
from sportlock.shared_kernel.base import ValueObject
from sportlock.shared_kernel.errors import DomainError
from sportlock.shared_kernel.ports import DesktopProtocol
from sportlock.training.application.training_history import TrainingHistory

log = logging.getLogger("sportlock")
TEST_SECONDS = 60
MANUAL_MINUTES = (10, 90)


def end_lock_event(
    lock_event_repository: LockEventRepositoryProtocol, key: str, outcome: LockOutcome, now: datetime
) -> None:
    """Record how a lock ended; a lock never recorded, or already ended, is left as it is."""
    event = lock_event_repository.get_by_id(key)
    if event is not None:
        lock_event_repository.update(event.ended(outcome, now))


def pending_override(override_repository: OverrideRepositoryProtocol, lock_key: str) -> Override | None:
    """The countdown still running for this lock, if any."""
    running = override_repository.find(OverrideFilters(lock_key=lock_key, pending=True, latest=1))
    return running[0] if running else None


class LockError(DomainError):
    """The lock request can't be honored."""


def policy_of(settings: Settings) -> Policy:
    """The recovery guardrails from settings."""
    return Policy(
        allow_rest_days=settings.allow_rest_days,
        max_rest_days_in_a_row=settings.max_rest_days_in_a_row,
        min_sessions_per_week=settings.min_sessions_per_week,
        recovery_minutes=settings.recovery_minutes,
    )


class PlanScheduledLockService:
    """Decides once per scheduled lock whether it is hard, recovery (maybe shorter) or a rest day.

    The decision is stored so it survives restarts.
    """

    def __init__(
        self,
        lock_plan_repository: LockPlanRepositoryProtocol,
        coach_plan_freshness: CoachPlanFreshness,
        training_history: TrainingHistory,
        lock_event_repository: LockEventRepositoryProtocol,
        desktop: DesktopProtocol,
    ) -> None:
        self.lock_plan_repository = lock_plan_repository
        self.coach_plan_freshness = coach_plan_freshness
        self.training_history = training_history
        self.lock_event_repository = lock_event_repository
        self.desktop = desktop

    def execute(self, window: Window, settings: Settings) -> LockPlan:
        """The stored plan, or a new decision."""
        if (plan := self.lock_plan_repository.get_by_id(window.key)) is not None:
            return plan
        coach = self.coach_plan_freshness.fresh_plan()
        advice = CoachAdvice(**coach.next_lock.model_dump()) if coach else None
        state = RecoveryState(
            last_credited=self.training_history.last_credited(),
            last_hard=self.training_history.last_hard(),
            sessions_last_7_days=len(self.training_history.credited_since(window.start - timedelta(days=7))),
            rest_days_in_a_row=rest_days_in_a_row(
                rest_days(self.lock_event_repository.find(LockEventFilters(outcomes=("rest",)))), window.day
            ),
        )
        decided = decide_lock(
            state, policy_of(settings), now=window.start, window_minutes=window.minutes, advice=advice
        )
        plan = LockPlan(
            key=window.key,
            mode=decided.mode,
            minutes=decided.minutes,
            reason=decided.reason,
            end=window.start + timedelta(minutes=decided.minutes),
        )
        self.lock_plan_repository.create(plan)
        log.info("lock %s planned as %s (%s min): %s", window.key, plan.mode, plan.minutes, plan.reason)
        if plan.mode == "rest":
            self.desktop.notify(f"Rest day: no lock at {window.start.strftime('%H:%M')}", plan.reason)
        return plan


class StartTestLockService:
    """A one-minute lock that can't be overridden and never counts."""

    def __init__(self, lock_runtime: LockRuntimeProtocol) -> None:
        self.lock_runtime = lock_runtime

    def execute(self, now: datetime) -> None:
        """Raises LockError while a lock is active."""
        if self.lock_runtime.current:
            raise LockError("a lock is already active")
        self.lock_runtime.test_lock = ActiveLock(
            key=f"test-{now.isoformat()}",
            kind="test",
            window=Window(start=now, end=now + timedelta(seconds=TEST_SECONDS)),
        )


class StartManualLockCommand(ValueObject):
    """Train now. Handled by `StartManualLockService`."""

    minutes: int = 30


class StartManualLockService:
    """A full lock started on request; it counts like a scheduled one."""

    def __init__(self, lock_runtime: LockRuntimeProtocol, manual_lock_repository: ManualLockRepositoryProtocol) -> None:
        self.lock_runtime = lock_runtime
        self.manual_lock_repository = manual_lock_repository

    def execute(self, command: StartManualLockCommand, now: datetime) -> None:
        """Raises LockError."""
        if self.lock_runtime.current:
            raise LockError("a lock is already active")
        if not MANUAL_MINUTES[0] <= command.minutes <= MANUAL_MINUTES[1]:
            raise LockError(f"minutes must be between {MANUAL_MINUTES[0]} and {MANUAL_MINUTES[1]}")
        self.manual_lock_repository.update(
            ActiveLock(
                key=f"manual-{now.isoformat()}",
                kind="manual",
                window=Window(start=now, end=now + timedelta(minutes=command.minutes)),
            )
        )


class RequestOverrideCommand(ValueObject):
    """Skip today's training. Handled by `RequestOverrideService`."""

    phrase: str


class RequestOverrideService:
    """Starts the countdown after which the lock ends; the phrase must be typed exactly."""

    def __init__(
        self,
        lock_runtime: LockRuntimeProtocol,
        override_repository: OverrideRepositoryProtocol,
        settings_state: SettingsState,
    ) -> None:
        self.lock_runtime = lock_runtime
        self.override_repository = override_repository
        self.settings_state = settings_state

    def execute(self, command: RequestOverrideCommand, now: datetime) -> None:
        """Raises LockError."""
        lock = self.lock_runtime.current
        if not lock:
            raise LockError("no active lock")
        if not lock.overridable:
            raise LockError("this lock can't be overridden")
        if " ".join(command.phrase.split()).lower() != self.settings_state.settings.override_phrase.lower():
            raise LockError("phrase doesn't match")
        if not pending_override(self.override_repository, lock.key):
            unlock_at = now + timedelta(seconds=self.settings_state.settings.override_wait_seconds)
            self.override_repository.create(Override(lock_key=lock.key, requested_at=now, unlock_at=unlock_at))


class CancelOverrideService:
    """Changed your mind: the lock stays."""

    def __init__(self, lock_runtime: LockRuntimeProtocol, override_repository: OverrideRepositoryProtocol) -> None:
        self.lock_runtime = lock_runtime
        self.override_repository = override_repository

    def execute(self, now: datetime) -> None:
        """No-op without a running countdown."""
        current = self.lock_runtime.current
        if current and (pending := pending_override(self.override_repository, current.key)):
            self.override_repository.update(pending.finished(now, cancelled=True))


class EndLockOnSessionFinishedService:
    """A finished session ends its lock early, and says what changes next time."""

    def __init__(
        self,
        lock_runtime: LockRuntimeProtocol,
        lock_event_repository: LockEventRepositoryProtocol,
        catalogue: Catalogue,
        desktop: DesktopProtocol,
    ) -> None:
        self.lock_runtime = lock_runtime
        self.lock_event_repository = lock_event_repository
        self.catalogue = catalogue
        self.desktop = desktop

    def execute(self, moves: list[LadderMove], now: datetime) -> None:
        """No-op without an active lock."""
        lock = self.lock_runtime.current
        if lock is None:
            return
        if lock.test:
            self.lock_runtime.test_lock = None
            return
        end_lock_event(self.lock_event_repository, lock.key, "completed", now)
        arrows = {"up": "↑", "add": "+", "down": "↓", "too-hard": "↓"}
        lines = [
            f"{arrows.get(m.proposal.rule, '·')} {self.catalogue.name(m.proposal.exercise)}"
            for m in moves
            if m.proposal.rule != "hold"
        ]
        self.desktop.notify("Session done ✓", "Next time: " + ", ".join(lines) if lines else "Same targets next time.")

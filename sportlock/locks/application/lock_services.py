"""Use cases of locks: the tick that locks and unlocks the desktop, and the athlete's requests
(test lock, session now, override)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sportlock.athlete.application.profile_services import equipment_of
from sportlock.athlete.domain.profile import ProfileRepositoryProtocol
from sportlock.coaching.application.fresh_plan import CoachPlanFreshness
from sportlock.exercises.domain.catalogue import Catalogue
from sportlock.locks.domain.locks import ActiveLock, LockPlan
from sportlock.locks.domain.repositories import (
    LockEventRepositoryProtocol,
    LockRuntimeProtocol,
    LockStateRepositoryProtocol,
    OverrideRepositoryProtocol,
)
from sportlock.locks.domain.schedule import Decision, Window, decide
from sportlock.progression.domain.ladders import LadderMove
from sportlock.recovery.domain.policy import CoachAdvice, Policy, RecoveryState, decide_lock, rest_days_in_a_row
from sportlock.settings.application.settings_services import SettingsState
from sportlock.settings.domain.settings import Settings
from sportlock.shared_kernel.base import ValueObject
from sportlock.shared_kernel.errors import DomainError
from sportlock.shared_kernel.ports import DesktopProtocol, LockScreenProtocol, WarningPopupProtocol
from sportlock.shared_kernel.time import epoch_ms
from sportlock.training.application.session_services import (
    BeginTrainingSessionCommand,
    BeginTrainingSessionService,
    CloseTrainingSessionService,
)
from sportlock.training.domain.repositories import TrainingHistoryProtocol

log = logging.getLogger("sportlock")
PLAN_AHEAD = timedelta(minutes=10)  # scheduled locks are decided this long before they start
TEST_SECONDS = 60
MANUAL_MINUTES = (10, 90)


class LockError(DomainError):
    """The lock request can't be honoured."""


def policy_of(settings: Settings) -> Policy:
    """The recovery guardrails from settings."""
    return Policy(allow_rest_days=settings.allow_rest_days, max_rest_days_in_a_row=settings.max_rest_days_in_a_row,
                  min_sessions_per_week=settings.min_sessions_per_week, recovery_minutes=settings.recovery_minutes)


class PlanScheduledLockService:
    """Decides once per scheduled lock whether it is hard, recovery (maybe shorter) or a rest day;
    the decision is stored so it survives restarts."""

    def __init__(self, lock_state: LockStateRepositoryProtocol, freshness: CoachPlanFreshness,
                 history: TrainingHistoryProtocol, lock_events: LockEventRepositoryProtocol,
                 desktop: DesktopProtocol) -> None:
        self.lock_state = lock_state
        self.freshness = freshness
        self.history = history
        self.lock_events = lock_events
        self.desktop = desktop

    def execute(self, window: Window, settings: Settings) -> LockPlan:
        """The stored plan, or a new decision."""
        if (plan := self.lock_state.plans().get(window.key)) is not None:
            return plan
        coach = self.freshness.fresh_plan()
        advice = CoachAdvice(**coach.next_lock.model_dump()) if coach else None
        state = RecoveryState(last_credited=self.history.last_credited(), last_hard=self.history.last_hard(),
                              sessions_last_7_days=len(self.history.credited_since(window.start - timedelta(days=7))),
                              rest_days_in_a_row=rest_days_in_a_row(self.lock_events.rest_days(), window.day))
        decided = decide_lock(state, policy_of(settings), now=window.start, window_minutes=window.minutes, advice=advice)
        plan = LockPlan(mode=decided.mode, minutes=decided.minutes, reason=decided.reason,
                        end=window.start + timedelta(minutes=decided.minutes))
        self.lock_state.save_plan(window.key, plan)
        log.info("lock %s planned as %s (%s min): %s", window.key, plan.mode, plan.minutes, plan.reason)
        if plan.mode == "rest":
            self.desktop.notify(f"Rest day: no lock at {window.start.strftime('%H:%M')}", plan.reason)
        return plan


class RunLockTickService:
    """Once a second: settle overrides, decide which lock should hold the screen, warn ahead of
    the next one, and lock or unlock."""

    def __init__(self, settings: SettingsState, runtime: LockRuntimeProtocol, lock_events: LockEventRepositoryProtocol,
                 overrides: OverrideRepositoryProtocol, lock_state: LockStateRepositoryProtocol,
                 history: TrainingHistoryProtocol, profiles: ProfileRepositoryProtocol, planner: PlanScheduledLockService,
                 begin_session: BeginTrainingSessionService, close_session: CloseTrainingSessionService,
                 freshness: CoachPlanFreshness, desktop: DesktopProtocol, lock_screen: LockScreenProtocol,
                 popup: WarningPopupProtocol) -> None:
        self.settings = settings
        self.runtime = runtime
        self.lock_events = lock_events
        self.overrides = overrides
        self.lock_state = lock_state
        self.history = history
        self.profiles = profiles
        self.planner = planner
        self.begin_session = begin_session
        self.close_session = close_session
        self.freshness = freshness
        self.desktop = desktop
        self.lock_screen = lock_screen
        self.popup = popup

    def decision(self, now: datetime) -> Decision:
        """What the schedule says now."""
        return decide(self.settings.settings, now, trained_days=self.history.trained_days(),
                      ended=self.lock_events.ended_early())

    def setup_complete(self) -> bool:
        """Scheduled locks wait for the profile."""
        return self.profiles.get() is not None

    def execute(self, now: datetime) -> Decision:
        """The schedule's decision, for the state file."""
        self._settle_override(now)
        decision = self.decision(now)
        if decision.next and self.setup_complete() and decision.next.start - PLAN_AHEAD <= now:
            self.planner.execute(decision.next, self.settings.settings)  # decide early so the warning can say what's coming
        wanted = self._wanted(now, decision)
        self._warn(decision, wanted)
        current = self.runtime.current
        if current and (wanted is None or wanted.key != current.key):
            self._leave(now)
        if wanted and self.runtime.current is None:
            self._enter(wanted, now)
        if self.runtime.current:
            self.lock_screen.ensure_shown()
        return decision

    def _settle_override(self, now: datetime) -> None:
        lock = self.runtime.current
        if not lock or not lock.overridable:
            return
        pending = self.overrides.pending(lock.key)
        if pending and now >= pending.unlock_at:
            self.overrides.finish(pending.id, now, cancelled=False)
            self.lock_events.ended(lock.key, "override", now)

    def _wanted(self, now: datetime, decision: Decision) -> ActiveLock | None:
        candidates = []
        if self.runtime.test_lock and now >= self.runtime.test_lock.window.end:
            self.runtime.test_lock = None
        if self.runtime.test_lock:
            candidates.append(self.runtime.test_lock)
        manual = self.lock_state.manual_lock()
        if manual:
            if now < manual.window.end and manual.key not in self.lock_events.ended_early():
                candidates.append(manual)
            else:
                self.lock_state.set_manual_lock(None)
        if decision.active and self.setup_complete():
            plan = self.planner.execute(decision.active, self.settings.settings)
            if plan.mode == "rest":
                if decision.active.key not in self.lock_events.ended_early():
                    self.lock_events.began(decision.active.key, decision.active.start, decision.active.end, now)
                    self.lock_events.ended(decision.active.key, "rest", now)
            elif now < plan.end:  # recovery locks can be shorter
                candidates.append(ActiveLock(key=decision.active.key, kind="scheduled", mode=plan.mode,
                                             window=Window(start=decision.active.start, end=plan.end)))
        # Stay under the lock already on screen while it is still due; overlaps don't swap sessions.
        for lock in candidates:
            if self.runtime.current and lock.key == self.runtime.current.key:
                return lock
        return candidates[0] if candidates else None

    def _warn(self, decision: Decision, wanted: ActiveLock | None) -> None:
        upcoming = decision.next
        if wanted or upcoming is None or decision.warning is None:
            return
        tag = f"{upcoming.key}:{decision.warning}"
        warned = self.lock_state.warned()
        if tag in warned:
            return
        plan = self.lock_state.plans().get(upcoming.key)
        if plan and plan.mode == "rest":
            return  # the rest-day notification already went out
        minutes = plan.minutes if plan else upcoming.minutes
        kind = "Recovery lock" if plan and plan.mode == "recovery" else "Training lock"
        headline = f"{kind} at {upcoming.start.strftime('%H:%M')}"
        self.desktop.notify(headline, f"Your desktop locks in {decision.warning} min for {minutes} min."
                            + (f" {plan.reason}" if plan and plan.reason else ""), urgent=decision.warning <= 2)
        # The first warning also gets a popup in front of everything: notifications are easy to miss.
        if self.settings.settings.warn_popup and not any(t.startswith(f"{upcoming.key}:") for t in warned):
            coach = self.freshness.fresh_plan()
            version = getattr(coach, plan.mode) if coach and plan and plan.mode in ("hard", "recovery") else None
            self.popup.show({"headline": headline, "start": epoch_ms(upcoming.start), "minutes": minutes,
                             "title": version.title if version else "", "reason": plan.reason if plan else "",
                             "recommendations": [r.model_dump() for r in coach.recommendations] if coach else [],
                             "theme": self.desktop.theme()})
        self.lock_state.add_warned(tag)

    def _enter(self, lock: ActiveLock, now: datetime) -> None:
        if self.desktop.system_lock_active():
            # The countdown keeps running; take over as soon as the user unlocks.
            self.runtime.waiting_for_omarchy = True
            return
        self.runtime.waiting_for_omarchy = False
        self.popup.close()
        if self.lock_state.desktop_to_restore() is None:
            self.lock_state.set_desktop_to_restore({"paused": self.desktop.pause_media(),
                                                    "stay_awake": self.desktop.idle_stay_awake()})
        self.desktop.set_idle_stay_awake(True)
        log.info("lock begins: %s (%s) until %s", lock.key, lock.kind, lock.window.end.isoformat())
        if not lock.test:
            self.lock_events.began(lock.key, lock.window.start, lock.window.end, now)
        self.begin_session.execute(BeginTrainingSessionCommand(
            kind=lock.kind, lock_key=lock.key, minutes=(lock.window.end - now).total_seconds() / 60,
            equipment=equipment_of(self.profiles, self.settings.settings),
            mode=lock.mode if lock.mode in ("hard", "recovery") else None), now)  # type: ignore[arg-type]
        self.runtime.current = lock

    def _leave(self, now: datetime) -> None:
        lock = self.runtime.current
        self.runtime.current = None
        if lock is None:
            return
        outcome = self.lock_events.outcome(lock.key)
        log.info("lock ends: %s (outcome %s)", lock.key, outcome or "expired")
        self.close_session.execute("overridden" if outcome == "override" else "abandoned", now)
        if not lock.test:
            self.lock_events.ended(lock.key, "expired", now)  # no-op if already ended early
        restore_desktop(self.lock_state, self.desktop)

    def release(self, now: datetime) -> None:
        """The service is stopping: leave the lock (stopping always unlocks)."""
        if self.runtime.current:
            self._leave(now)


def restore_desktop(lock_state: LockStateRepositoryProtocol, desktop: DesktopProtocol) -> None:
    """Resume the media and the idle setting saved when the lock began (also after a crash)."""
    saved = lock_state.desktop_to_restore()
    if saved is None:
        return
    desktop.resume_media(saved.get("paused") or [])
    if saved.get("stay_awake") is not None:
        desktop.set_idle_stay_awake(bool(saved["stay_awake"]))
    lock_state.set_desktop_to_restore(None)


class StartTestLockService:
    """A one-minute lock that can't be overridden and never counts."""

    def __init__(self, runtime: LockRuntimeProtocol) -> None:
        self.runtime = runtime

    def execute(self, now: datetime) -> None:
        """Raises LockError while a lock is active."""
        if self.runtime.current:
            raise LockError("a lock is already active")
        self.runtime.test_lock = ActiveLock(key=f"test-{now.isoformat()}", kind="test",
                                            window=Window(start=now, end=now + timedelta(seconds=TEST_SECONDS)))


class StartManualLockCommand(ValueObject):
    """Train now. Handled by `StartManualLockService`."""

    minutes: int = 30


class StartManualLockService:
    """A full lock started on request; it counts like a scheduled one."""

    def __init__(self, runtime: LockRuntimeProtocol, lock_state: LockStateRepositoryProtocol) -> None:
        self.runtime = runtime
        self.lock_state = lock_state

    def execute(self, command: StartManualLockCommand, now: datetime) -> None:
        """Raises LockError."""
        if self.runtime.current:
            raise LockError("a lock is already active")
        if not MANUAL_MINUTES[0] <= command.minutes <= MANUAL_MINUTES[1]:
            raise LockError(f"minutes must be between {MANUAL_MINUTES[0]} and {MANUAL_MINUTES[1]}")
        self.lock_state.set_manual_lock(ActiveLock(key=f"manual-{now.isoformat()}", kind="manual",
                                                   window=Window(start=now, end=now + timedelta(minutes=command.minutes))))


class RequestOverrideCommand(ValueObject):
    """Skip today's training. Handled by `RequestOverrideService`."""

    phrase: str


class RequestOverrideService:
    """Starts the countdown after which the lock ends; the phrase must be typed exactly."""

    def __init__(self, runtime: LockRuntimeProtocol, overrides: OverrideRepositoryProtocol, settings: SettingsState) -> None:
        self.runtime = runtime
        self.overrides = overrides
        self.settings = settings

    def execute(self, command: RequestOverrideCommand, now: datetime) -> None:
        """Raises LockError."""
        lock = self.runtime.current
        if not lock:
            raise LockError("no active lock")
        if not lock.overridable:
            raise LockError("this lock can't be overridden")
        if " ".join(command.phrase.split()).lower() != self.settings.settings.override_phrase.lower():
            raise LockError("phrase doesn't match")
        if not self.overrides.pending(lock.key):
            self.overrides.start(lock.key, now, now + timedelta(seconds=self.settings.settings.override_wait_seconds))


class CancelOverrideService:
    """Changed your mind: the lock stays."""

    def __init__(self, runtime: LockRuntimeProtocol, overrides: OverrideRepositoryProtocol) -> None:
        self.runtime = runtime
        self.overrides = overrides

    def execute(self, now: datetime) -> None:
        """No-op without a running countdown."""
        if self.runtime.current and (pending := self.overrides.pending(self.runtime.current.key)):
            self.overrides.finish(pending.id, now, cancelled=True)


class EndLockOnSessionFinishedService:
    """A finished session ends its lock early, and says what changes next time."""

    def __init__(self, runtime: LockRuntimeProtocol, lock_events: LockEventRepositoryProtocol, catalogue: Catalogue,
                 desktop: DesktopProtocol) -> None:
        self.runtime = runtime
        self.lock_events = lock_events
        self.catalogue = catalogue
        self.desktop = desktop

    def execute(self, moves: list[LadderMove], now: datetime) -> None:
        """No-op without an active lock."""
        lock = self.runtime.current
        if lock is None:
            return
        if lock.test:
            self.runtime.test_lock = None
            return
        self.lock_events.ended(lock.key, "completed", now)
        arrows = {"up": "↑", "add": "+", "down": "↓", "too-hard": "↓"}
        lines = [f"{arrows.get(m.proposal.rule, '·')} {self.catalogue.name(m.proposal.exercise)}"
                 for m in moves if m.proposal.rule != "hold"]
        self.desktop.notify("Session done ✓", "Next time: " + ", ".join(lines) if lines else "Same targets next time.")

"""The once-a-second lock tick, one use case per step.

Each tick settles overrides, decides the schedule, chooses the lock that should hold the screen, warns
ahead of the next one, then enters or leaves the lock. `RunLockTickService` runs the steps in that order.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sportlock.athlete.application.profile_services import equipment_of
from sportlock.athlete.domain.profile import ProfileRepositoryProtocol
from sportlock.coaching.application.fresh_plan import CoachPlanFreshness
from sportlock.coaching.application.replacement_services import PendingReplacementsService
from sportlock.locks.application.lock_services import PlanScheduledLockService
from sportlock.locks.domain.locks import ActiveLock
from sportlock.locks.domain.repositories import (
    LockEventRepositoryProtocol,
    LockRuntimeProtocol,
    LockStateRepositoryProtocol,
    OverrideRepositoryProtocol,
)
from sportlock.locks.domain.schedule import Decision, Window, decide
from sportlock.music.domain.music import FULL_VOLUME
from sportlock.music.domain.ports import MusicPlayerProtocol
from sportlock.settings.application.settings_services import SettingsState
from sportlock.shared_kernel.ports import DesktopProtocol, LockScreenProtocol, WarningPopupProtocol
from sportlock.shared_kernel.time import epoch_ms
from sportlock.training.application.session_services import (
    BeginTrainingSessionCommand,
    BeginTrainingSessionService,
    CloseTrainingSessionService,
)
from sportlock.training.domain.repositories import TrainingHistoryProtocol

log = logging.getLogger("sportlock")
URGENT_WARNING_MINUTES = 2
PLAN_AHEAD = timedelta(minutes=10)  # scheduled locks are decided this long before they start


class GetScheduleDecisionService:
    """What the schedule says now: the active lock, the next one, and whether to warn."""

    def __init__(
        self, settings: SettingsState, history: TrainingHistoryProtocol, lock_events: LockEventRepositoryProtocol
    ) -> None:
        self.settings = settings
        self.history = history
        self.lock_events = lock_events

    def execute(self, now: datetime) -> Decision:
        """The schedule's decision at this time."""
        return decide(
            self.settings.settings, now, trained_days=self.history.trained_days(), ended=self.lock_events.ended_early()
        )


class CheckSetupCompleteService:
    """Scheduled locks wait until the athlete has filled in the profile."""

    def __init__(self, profiles: ProfileRepositoryProtocol) -> None:
        self.profiles = profiles

    def execute(self) -> bool:
        """Whether the profile exists."""
        return self.profiles.get() is not None


class SettleOverrideService:
    """Ends the lock on screen once its override countdown has run out."""

    def __init__(
        self,
        runtime: LockRuntimeProtocol,
        overrides: OverrideRepositoryProtocol,
        lock_events: LockEventRepositoryProtocol,
    ) -> None:
        self.runtime = runtime
        self.overrides = overrides
        self.lock_events = lock_events

    def execute(self, now: datetime) -> None:
        """Finish a pending override that is due, and record the lock as ended by it."""
        lock = self.runtime.current
        if not lock or not lock.overridable:
            return
        pending = self.overrides.pending(lock.key)
        if pending and now >= pending.unlock_at:
            self.overrides.finish(pending.id, now, cancelled=False)
            self.lock_events.ended(lock.key, "override", now)


class ChooseLockService:
    """Picks the lock that should hold the screen: a test lock, a lock started by hand, or a scheduled one.

    It also plans the next scheduled lock ahead of time, so the warning can say what is coming.
    """

    def __init__(
        self,
        runtime: LockRuntimeProtocol,
        lock_state: LockStateRepositoryProtocol,
        lock_events: LockEventRepositoryProtocol,
        planner: PlanScheduledLockService,
        setup: CheckSetupCompleteService,
        settings: SettingsState,
    ) -> None:
        self.runtime = runtime
        self.lock_state = lock_state
        self.lock_events = lock_events
        self.planner = planner
        self.setup = setup
        self.settings = settings

    def _manual_candidate(self, now: datetime) -> ActiveLock | None:
        """The lock started by hand, or None; a finished one is forgotten."""
        manual = self.lock_state.manual_lock()
        if not manual:
            return None
        if now < manual.window.end and manual.key not in self.lock_events.ended_early():
            return manual
        self.lock_state.set_manual_lock(None)
        return None

    def _scheduled_candidate(self, now: datetime, decision: Decision) -> ActiveLock | None:
        """The scheduled lock due now, or None; a rest day is recorded as a lock that ended at once."""
        if not decision.active or not self.setup.execute():
            return None
        plan = self.planner.execute(decision.active, self.settings.settings)
        if plan.mode == "rest":
            if decision.active.key not in self.lock_events.ended_early():
                self.lock_events.began(decision.active.key, decision.active.start, decision.active.end, now)
                self.lock_events.ended(decision.active.key, "rest", now)
            return None
        if now >= plan.end:  # recovery locks can be shorter
            return None
        return ActiveLock(
            key=decision.active.key,
            kind="scheduled",
            mode=plan.mode,
            window=Window(start=decision.active.start, end=plan.end),
        )

    def execute(self, now: datetime, decision: Decision) -> ActiveLock | None:
        """The lock that should be on screen now, or None."""
        if decision.next and self.setup.execute() and decision.next.start - PLAN_AHEAD <= now:
            self.planner.execute(decision.next, self.settings.settings)
        if self.runtime.test_lock and now >= self.runtime.test_lock.window.end:
            self.runtime.test_lock = None
        candidates = [
            lock
            for lock in (
                self.runtime.test_lock,
                self._manual_candidate(now),
                self._scheduled_candidate(now, decision),
            )
            if lock
        ]
        # Stay under the lock already on screen while it is still due; overlaps don't swap sessions.
        for lock in candidates:
            if self.runtime.current and lock.key == self.runtime.current.key:
                return lock
        return candidates[0] if candidates else None


class WarnOfNextLockService:
    """Notifies before the next lock, and shows the warning popup with the first notification."""

    def __init__(
        self,
        settings: SettingsState,
        lock_state: LockStateRepositoryProtocol,
        freshness: CoachPlanFreshness,
        replacements: PendingReplacementsService,
        desktop: DesktopProtocol,
        popup: WarningPopupProtocol,
    ) -> None:
        self.settings = settings
        self.lock_state = lock_state
        self.freshness = freshness
        self.replacements = replacements
        self.desktop = desktop
        self.popup = popup

    def execute(self, decision: Decision, wanted: ActiveLock | None) -> None:
        """Send each warning of the schedule once; nothing while a lock is wanted or on a rest day."""
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
        self.desktop.notify(
            headline,
            f"Your desktop locks in {decision.warning} min for {minutes} min."
            + (f" {plan.reason}" if plan and plan.reason else ""),
            urgent=decision.warning <= URGENT_WARNING_MINUTES,
        )
        # The first warning also gets a popup in front of everything: notifications are easy to miss.
        if self.settings.settings.warn_popup and not any(t.startswith(f"{upcoming.key}:") for t in warned):
            coach = self.freshness.fresh_plan()
            version = getattr(coach, plan.mode) if coach and plan and plan.mode in ("hard", "recovery") else None
            self.popup.show(
                {
                    "headline": headline,
                    "start": epoch_ms(upcoming.start),
                    "minutes": minutes,
                    "title": version.title if version else "",
                    "reason": plan.reason if plan else "",
                    "recommendations": [r.model_dump() for r in coach.recommendations] if coach else [],
                    "replacements": [r.to_dict() for r in self.replacements.execute()],
                    "theme": self.desktop.theme(),
                }
            )
        self.lock_state.add_warned(tag)


class PrepareDesktopForLockService:
    """Readies the desktop for training: closes the warning, pauses media, keeps the screen awake."""

    def __init__(
        self,
        lock_state: LockStateRepositoryProtocol,
        desktop: DesktopProtocol,
        music: MusicPlayerProtocol,
        popup: WarningPopupProtocol,
    ) -> None:
        self.lock_state = lock_state
        self.desktop = desktop
        self.music = music
        self.popup = popup

    def execute(self) -> bool:
        """Whether the desktop is ready; False while Omarchy's own lock screen is up."""
        if self.desktop.system_lock_active():
            return False
        self.popup.close()
        if self.lock_state.desktop_to_restore() is None:
            # Spotify plays at full volume during training; its volume is put back afterwards.
            volume = self.music.now_playing().volume
            self.lock_state.set_desktop_to_restore(
                {
                    "paused": self.desktop.pause_media(),
                    "stay_awake": self.desktop.idle_stay_awake(),
                    "music_volume": volume,
                }
            )
            if volume is not None:
                self.music.set_volume(FULL_VOLUME)
        self.desktop.set_idle_stay_awake(True)
        return True


class EnterLockService:
    """Puts a lock on screen and begins its training session."""

    def __init__(
        self,
        runtime: LockRuntimeProtocol,
        prepare_desktop: PrepareDesktopForLockService,
        lock_events: LockEventRepositoryProtocol,
        begin_session: BeginTrainingSessionService,
        profiles: ProfileRepositoryProtocol,
        settings: SettingsState,
    ) -> None:
        self.runtime = runtime
        self.prepare_desktop = prepare_desktop
        self.lock_events = lock_events
        self.begin_session = begin_session
        self.profiles = profiles
        self.settings = settings

    def execute(self, lock: ActiveLock, now: datetime) -> None:
        """Enter the lock, or wait for Omarchy's lock screen to go away; the countdown keeps running."""
        ready = self.prepare_desktop.execute()
        self.runtime.waiting_for_omarchy = not ready
        if not ready:
            return
        log.info("lock begins: %s (%s) until %s", lock.key, lock.kind, lock.window.end.isoformat())
        if not lock.test:
            self.lock_events.began(lock.key, lock.window.start, lock.window.end, now)
        self.begin_session.execute(
            BeginTrainingSessionCommand(
                kind=lock.kind,
                lock_key=lock.key,
                minutes=(lock.window.end - now).total_seconds() / 60,
                equipment=equipment_of(self.profiles, self.settings.settings),
                mode=lock.mode if lock.mode in ("hard", "recovery") else None,
            ),
            now,
        )  # type: ignore[arg-type]
        self.runtime.current = lock


def restore_desktop(
    lock_state: LockStateRepositoryProtocol, desktop: DesktopProtocol, music: MusicPlayerProtocol
) -> None:
    """Resume the media, Spotify's volume and the idle setting saved when the lock began (also after a crash)."""
    saved = lock_state.desktop_to_restore()
    if saved is None:
        return
    if saved.get("music_volume") is not None and music.now_playing().running:
        music.set_volume(float(saved["music_volume"]))
    desktop.resume_media(saved.get("paused") or [])
    if saved.get("stay_awake") is not None:
        desktop.set_idle_stay_awake(bool(saved["stay_awake"]))
    lock_state.set_desktop_to_restore(None)


class LeaveLockService:
    """Takes the lock off screen, closes its session and gives the desktop back. Stopping always unlocks."""

    def __init__(
        self,
        runtime: LockRuntimeProtocol,
        lock_events: LockEventRepositoryProtocol,
        close_session: CloseTrainingSessionService,
        lock_state: LockStateRepositoryProtocol,
        desktop: DesktopProtocol,
        music: MusicPlayerProtocol,
    ) -> None:
        self.runtime = runtime
        self.lock_events = lock_events
        self.close_session = close_session
        self.lock_state = lock_state
        self.desktop = desktop
        self.music = music

    def execute(self, now: datetime) -> None:
        """Leave the lock on screen, if any."""
        lock = self.runtime.current
        self.runtime.current = None
        if lock is None:
            return
        outcome = self.lock_events.outcome(lock.key)
        log.info("lock ends: %s (outcome %s)", lock.key, outcome or "expired")
        self.close_session.execute("overridden" if outcome == "override" else "abandoned", now)
        if not lock.test:
            self.lock_events.ended(lock.key, "expired", now)  # no-op if already ended early
        restore_desktop(self.lock_state, self.desktop, self.music)


class SwitchLockService:
    """Makes the screen match the chosen lock: leave a lock no longer wanted, enter the wanted one."""

    def __init__(
        self,
        runtime: LockRuntimeProtocol,
        enter: EnterLockService,
        leave: LeaveLockService,
        lock_screen: LockScreenProtocol,
    ) -> None:
        self.runtime = runtime
        self.enter = enter
        self.leave = leave
        self.lock_screen = lock_screen

    def execute(self, wanted: ActiveLock | None, now: datetime) -> None:
        """Switch locks, and show the lock screen again if it closed while a lock is on."""
        current = self.runtime.current
        if current and (wanted is None or wanted.key != current.key):
            self.leave.execute(now)
        if wanted and self.runtime.current is None:
            self.enter.execute(wanted, now)
        if self.runtime.current:
            self.lock_screen.ensure_shown()


class RunLockTickService:
    """Once a second: settle overrides, decide the schedule, choose the lock, warn, then lock or unlock."""

    def __init__(
        self,
        settle_override: SettleOverrideService,
        schedule: GetScheduleDecisionService,
        choose_lock: ChooseLockService,
        warn: WarnOfNextLockService,
        switch_lock: SwitchLockService,
    ) -> None:
        self.settle_override = settle_override
        self.schedule = schedule
        self.choose_lock = choose_lock
        self.warn = warn
        self.switch_lock = switch_lock

    def execute(self, now: datetime) -> Decision:
        """Run one tick; returns the schedule's decision, for the state file."""
        self.settle_override.execute(now)
        decision = self.schedule.execute(now)
        wanted = self.choose_lock.execute(now, decision)
        self.warn.execute(decision, wanted)
        self.switch_lock.execute(wanted, now)
        return decision

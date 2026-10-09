"""Composition root: builds the adapters and wires them into the use cases. The only module that
knows every concrete class; tests pass fakes for the desktop, the screens, the coach and the clock."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sportlock.athlete.application.profile_services import SaveProfileService
from sportlock.athlete.infrastructure.kv_profile_repository import KvProfileRepository
from sportlock.calendar.application.build_calendar_service import BuildCalendarService
from sportlock.coaching.application.fresh_plan import CoachPlanFreshness
from sportlock.coaching.application.memory_services import ForgetMemoryNoteService, GetMemoryService
from sportlock.coaching.application.run_coach_service import RunCoachService
from sportlock.coaching.domain.ports import CoachProtocol
from sportlock.coaching.infrastructure.claude_coach import ClaudeCoach
from sportlock.coaching.infrastructure.sqlite_repositories import (
    KvCoachPlanRepository,
    SqliteCoachMemoryRepository,
    SqliteCoachRunLog,
)
from sportlock.exercises.domain.catalogue import Catalogue
from sportlock.exercises.infrastructure.details_repository import (
    LIBRARY_DIR,
    FileExerciseDetailsRepository,
    load_catalogue,
)
from sportlock.locks.application.lock_services import (
    CancelOverrideService,
    EndLockOnSessionFinishedService,
    PlanScheduledLockService,
    RequestOverrideService,
    RunLockTickService,
    StartManualLockService,
    StartTestLockService,
)
from sportlock.locks.infrastructure.in_memory_runtime import InMemoryLockRuntime
from sportlock.locks.infrastructure.quickshell import QuickshellLockScreen, QuickshellWarningPopup
from sportlock.locks.infrastructure.sqlite_repositories import (
    KvLockStateRepository,
    SqliteLockEventRepository,
    SqliteOverrideRepository,
)
from sportlock.progression.application.apply_session_progression_service import ApplySessionProgressionService
from sportlock.progression.infrastructure.sqlite_ladder_repository import SqliteLadderRepository
from sportlock.settings.application.settings_services import (
    GetSettingsService,
    ReloadSettingsService,
    SaveSettingsService,
    SettingsState,
)
from sportlock.settings.infrastructure.config_toml import CONFIG_PATH, TomlSettingsRepository
from sportlock.shared_kernel.infrastructure.database import DB_PATH, Database
from sportlock.shared_kernel.infrastructure.omarchy_desktop import OmarchyDesktop
from sportlock.shared_kernel.ports import ClockProtocol, DesktopProtocol, LockScreenProtocol, WarningPopupProtocol
from sportlock.training.application.session_services import (
    BeginTrainingSessionService,
    CloseTrainingSessionService,
    RecordTrainingActionService,
)
from sportlock.training.application.snapshot import TrainingSnapshotService
from sportlock.training.infrastructure.sqlite_repositories import SqliteTrainingHistory, SqliteTrainingSessionRepository

REPO_DIR = Path(__file__).resolve().parents[2]
CLI = REPO_DIR / "bin" / "sportlock"
SEARCH_TOOL = REPO_DIR / "tools" / "nlm_search.py"


class SystemClock(ClockProtocol):
    """The real wall clock."""

    def now(self) -> datetime:
        """Local time to the second."""
        return datetime.now().replace(microsecond=0)


class Container:
    """Everything the service and the CLI use, built once."""

    def __init__(self, *, db_path: Path = DB_PATH, config_path: Path = CONFIG_PATH, library_dir: Path = LIBRARY_DIR,
                 state_path: Path | None = None, desktop: DesktopProtocol | None = None,
                 lock_screen: LockScreenProtocol | None = None, popup: WarningPopupProtocol | None = None,
                 coach: CoachProtocol | None = None, clock: ClockProtocol | None = None,
                 catalogue: Catalogue | None = None) -> None:
        self.clock = clock or SystemClock()
        self.desktop = desktop or OmarchyDesktop()
        self.database = Database(db_path)
        self.catalogue = catalogue or load_catalogue()
        self.details = FileExerciseDetailsRepository(library_dir)

        self.settings = SettingsState()
        self.settings_repository = TomlSettingsRepository(config_path)
        self.reload_settings = ReloadSettingsService(self.settings, self.settings_repository, self.desktop)
        self.reload_settings.execute(decision=None, now=self.clock.now(), force=True)
        self.get_settings = GetSettingsService(self.settings, self.settings_repository)
        self.save_settings = SaveSettingsService(self.settings, self.settings_repository, self.reload_settings)

        self.profiles = KvProfileRepository(self.database)
        self.ladders = SqliteLadderRepository(self.database)
        self.sessions = SqliteTrainingSessionRepository(self.database)
        self.history = SqliteTrainingHistory(self.database)
        self.lock_events = SqliteLockEventRepository(self.database)
        self.overrides = SqliteOverrideRepository(self.database)
        self.lock_state = KvLockStateRepository(self.database)
        self.coach_plans = KvCoachPlanRepository(self.database)
        self.coach_runs = SqliteCoachRunLog(self.database)
        self.memory = SqliteCoachMemoryRepository(self.database)
        self.runtime = InMemoryLockRuntime()

        self.lock_screen = lock_screen or QuickshellLockScreen(state_path or Path("/dev/null"), CLI)
        self.popup = popup or QuickshellWarningPopup()
        self.coach = coach or ClaudeCoach(self.settings.settings.notebook_id, SEARCH_TOOL)

        self.freshness = CoachPlanFreshness(self.coach_plans, self.history, self.profiles)
        self.progression = ApplySessionProgressionService(self.ladders, self.catalogue)
        self.save_profile = SaveProfileService(self.profiles, self.ladders)
        self.begin_session = BeginTrainingSessionService(self.sessions, self.history, self.ladders, self.freshness,
                                                         self.catalogue)
        self.close_session = CloseTrainingSessionService(self.sessions, self.progression)
        self.record_training_action = RecordTrainingActionService(self.sessions, self.catalogue, self.progression)
        self.training_snapshot = TrainingSnapshotService(self.sessions, self.history, self.catalogue, self.details)
        self.plan_lock = PlanScheduledLockService(self.lock_state, self.freshness, self.history, self.lock_events,
                                                  self.desktop)
        self.tick = RunLockTickService(self.settings, self.runtime, self.lock_events, self.overrides, self.lock_state,
                                       self.history, self.profiles, self.plan_lock, self.begin_session,
                                       self.close_session, self.freshness, self.desktop, self.lock_screen, self.popup)
        self.start_test_lock = StartTestLockService(self.runtime)
        self.start_manual_lock = StartManualLockService(self.runtime, self.lock_state)
        self.request_override = RequestOverrideService(self.runtime, self.overrides, self.settings)
        self.cancel_override = CancelOverrideService(self.runtime, self.overrides)
        self.end_lock_on_finish = EndLockOnSessionFinishedService(self.runtime, self.lock_events, self.catalogue,
                                                                  self.desktop)
        self.run_coach = RunCoachService(self.coach, self.catalogue, self.coach_plans, self.coach_runs, self.memory,
                                         self.ladders, self.history, self.profiles, self.lock_events, self.desktop,
                                         self.clock)
        self.get_memory = GetMemoryService(self.memory)
        self.forget_memory_note = ForgetMemoryNoteService(self.memory, self.clock)
        self.calendar = BuildCalendarService(self.history, self.lock_events, self.lock_state, self.freshness,
                                             self.catalogue)

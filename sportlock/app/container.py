"""Composition root: builds the adapters and wires them into the use cases.

The only module that knows every concrete class; tests pass fakes for the desktop, the screens, the coach and the clock.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sportlock.athlete.application.profile_services import SaveProfileService
from sportlock.athlete.infrastructure.kv_profile_repository import KvProfileRepository
from sportlock.calendar.application.build_calendar_service import BuildCalendarService
from sportlock.coaching.application.coach_context_service import (
    BuildCoachContextService,
    DescribeLaddersForCoachService,
    RecallCoachMemoryService,
)
from sportlock.coaching.application.fresh_plan import CoachPlanFreshness
from sportlock.coaching.application.memory_services import ForgetMemoryNoteService, GetMemoryService
from sportlock.coaching.application.replacement_services import (
    AnswerReplacementService,
    KeepReplacedExerciseService,
    PendingReplacementsService,
)
from sportlock.coaching.application.run_coach_service import (
    ApplyLadderOverridesService,
    LogCoachRunService,
    PrepareCoachRunService,
    RunCoachService,
    StoreCoachPlanService,
)
from sportlock.coaching.domain.ports import CoachProtocol
from sportlock.coaching.infrastructure.claude_coach import ClaudeCoach
from sportlock.coaching.infrastructure.sqlite_repositories import (
    KvCoachPlanRepository,
    SqliteCoachMemoryRepository,
    SqliteCoachReviewRepository,
    SqliteCoachRunRepository,
    SqliteMemoryNoteVersionRepository,
    SqliteReplacementAnswerRepository,
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
    StartManualLockService,
    StartTestLockService,
)
from sportlock.locks.application.tick_services import (
    CheckSetupCompleteService,
    ChooseLockService,
    EnterLockService,
    GetScheduleDecisionService,
    LeaveLockService,
    PrepareDesktopForLockService,
    RunLockTickService,
    SettleOverrideService,
    ShowWarningPopupService,
    SwitchLockService,
    WarnOfNextLockService,
)
from sportlock.locks.infrastructure.in_memory_runtime import InMemoryLockRuntime
from sportlock.locks.infrastructure.quickshell import QuickshellLockScreen, QuickshellWarningPopup
from sportlock.locks.infrastructure.sqlite_repositories import (
    KvDesktopToRestoreRepository,
    KvGivenWarningRepository,
    KvLockPlanRepository,
    KvManualLockRepository,
    SqliteLockEventRepository,
    SqliteOverrideRepository,
)
from sportlock.music.application.music_account_services import (
    ConnectMusicAccountService,
    DisconnectMusicAccountService,
    GetMusicAccountService,
)
from sportlock.music.application.music_services import ControlMusicService, MusicSnapshotService
from sportlock.music.domain.ports import MusicAccountProtocol, MusicLibraryProtocol, MusicPlayerProtocol
from sportlock.music.infrastructure.spotify_mpris import SpotifyMprisPlayer
from sportlock.music.infrastructure.spotify_web import SpotifyWebLibrary
from sportlock.progression.application.apply_session_progression_service import ApplySessionProgressionService
from sportlock.progression.infrastructure.sqlite_ladder_repository import (
    SqliteLadderMoveRecordRepository,
    SqliteLadderPositionRepository,
)
from sportlock.reporting.application.report_services import (
    BuildReportService,
    TrainingRecords,
    WriteCoachReviewService,
)
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
from sportlock.training.application.training_history import TrainingHistory
from sportlock.training.infrastructure.sqlite_repositories import (
    SqliteCompletedExerciseRepository,
    SqliteCountedSessionRepository,
    SqliteExerciseTimingRepository,
    SqliteSessionSummaryRepository,
    SqliteTrainingSessionRepository,
    SqliteTransitionGapRepository,
)

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

    def __init__(  # noqa: PLR0913 (the composition root takes one keyword per replaceable adapter)
        self,
        *,
        db_path: Path = DB_PATH,
        config_path: Path = CONFIG_PATH,
        library_dir: Path = LIBRARY_DIR,
        state_path: Path | None = None,
        desktop: DesktopProtocol | None = None,
        lock_screen: LockScreenProtocol | None = None,
        warning_popup: WarningPopupProtocol | None = None,
        coach: CoachProtocol | None = None,
        clock: ClockProtocol | None = None,
        catalogue: Catalogue | None = None,
        music_player: MusicPlayerProtocol | None = None,
        music_library: MusicLibraryProtocol | None = None,
        music_account: MusicAccountProtocol | None = None,
    ) -> None:
        self.clock = clock or SystemClock()
        self.desktop = desktop or OmarchyDesktop()
        self.database = Database(db_path)
        self.catalogue = catalogue or load_catalogue()
        self.exercise_details_repository = FileExerciseDetailsRepository(library_dir)

        self._build_settings(config_path)
        self._build_repositories()
        self._build_adapters(
            state_path=state_path,
            lock_screen=lock_screen,
            popup=warning_popup,
            coach=coach,
            music_player=music_player,
            music_library=music_library,
            music_account=music_account,
        )
        self._build_training_services()
        self._build_lock_services()
        self._build_coach_services()
        self._build_music_services()

    def _build_settings(self, config_path: Path) -> None:
        self.settings_state = SettingsState()
        self.settings_repository = TomlSettingsRepository(config_path)
        self.reload_settings_service = ReloadSettingsService(
            self.settings_state, self.settings_repository, self.settings_repository, self.desktop
        )
        self.reload_settings_service.execute(decision=None, now=self.clock.now(), force=True)
        self.get_settings_service = GetSettingsService(self.settings_state, self.settings_repository)
        self.save_settings_service = SaveSettingsService(
            self.settings_state, self.settings_repository, self.reload_settings_service
        )

    def _build_repositories(self) -> None:
        self.profile_repository = KvProfileRepository(self.database)
        self.ladder_position_repository = SqliteLadderPositionRepository(self.database)
        self.ladder_move_record_repository = SqliteLadderMoveRecordRepository(self.database)
        self.training_session_repository = SqliteTrainingSessionRepository(self.database)
        self.training_history = TrainingHistory(
            SqliteSessionSummaryRepository(self.database),
            SqliteExerciseTimingRepository(self.database),
            SqliteTransitionGapRepository(self.database),
            SqliteCompletedExerciseRepository(self.database),
            SqliteCountedSessionRepository(self.database),
        )
        self.lock_event_repository = SqliteLockEventRepository(self.database)
        self.override_repository = SqliteOverrideRepository(self.database)
        self.lock_plan_repository = KvLockPlanRepository(self.database)
        self.manual_lock_repository = KvManualLockRepository(self.database)
        self.given_warning_repository = KvGivenWarningRepository(self.database)
        self.desktop_to_restore_repository = KvDesktopToRestoreRepository(self.database)
        self.coach_plan_repository = KvCoachPlanRepository(self.database)
        self.coach_run_repository = SqliteCoachRunRepository(self.database)
        self.coach_memory_repository = SqliteCoachMemoryRepository(self.database)
        self.memory_note_version_repository = SqliteMemoryNoteVersionRepository(self.database)
        self.replacement_answer_repository = SqliteReplacementAnswerRepository(self.database)
        self.coach_review_repository = SqliteCoachReviewRepository(self.database)
        self.lock_runtime = InMemoryLockRuntime()

    def _build_adapters(  # noqa: PLR0913 (one keyword per replaceable adapter, as in __init__)
        self,
        *,
        state_path: Path | None,
        lock_screen: LockScreenProtocol | None,
        popup: WarningPopupProtocol | None,
        coach: CoachProtocol | None,
        music_player: MusicPlayerProtocol | None,
        music_library: MusicLibraryProtocol | None,
        music_account: MusicAccountProtocol | None,
    ) -> None:
        self.lock_screen = lock_screen or QuickshellLockScreen(state_path or Path("/dev/null"), CLI)
        self.warning_popup = popup or QuickshellWarningPopup(CLI)
        self.music_player = music_player or SpotifyMprisPlayer()
        spotify_web = SpotifyWebLibrary(self.database)
        self.music_library = music_library or spotify_web
        self.music_account = music_account or spotify_web
        self.coach = coach or ClaudeCoach(self.settings_state.settings.notebook_id, SEARCH_TOOL)

    def _build_training_services(self) -> None:
        self.coach_plan_freshness = CoachPlanFreshness(
            self.coach_plan_repository, self.training_history, self.profile_repository
        )
        self.apply_session_progression_service = ApplySessionProgressionService(
            self.ladder_position_repository, self.ladder_move_record_repository, self.catalogue
        )
        self.save_profile_service = SaveProfileService(self.profile_repository, self.ladder_position_repository)
        self.begin_training_session_service = BeginTrainingSessionService(
            self.training_session_repository,
            self.training_history,
            self.ladder_position_repository,
            self.coach_plan_freshness,
            self.catalogue,
        )
        self.close_training_session_service = CloseTrainingSessionService(
            self.training_session_repository, self.apply_session_progression_service
        )
        self.record_training_action_service = RecordTrainingActionService(
            self.training_session_repository, self.catalogue, self.apply_session_progression_service
        )
        self.training_snapshot_service = TrainingSnapshotService(
            self.training_session_repository, self.training_history, self.catalogue, self.exercise_details_repository
        )
        self.pending_replacements_service = PendingReplacementsService(
            self.training_history,
            self.ladder_position_repository,
            self.ladder_move_record_repository,
            self.replacement_answer_repository,
            self.catalogue,
        )
        self.answer_replacement_service = AnswerReplacementService(
            self.pending_replacements_service,
            self.replacement_answer_repository,
            KeepReplacedExerciseService(
                self.ladder_position_repository,
                self.ladder_move_record_repository,
                self.coach_plan_repository,
                self.coach_plan_freshness,
                self.training_session_repository,
                self.catalogue,
            ),
        )

    def _build_lock_services(self) -> None:
        self.plan_scheduled_lock_service = PlanScheduledLockService(
            self.lock_plan_repository,
            self.coach_plan_freshness,
            self.training_history,
            self.lock_event_repository,
            self.desktop,
        )
        self.get_schedule_decision_service = GetScheduleDecisionService(
            self.settings_state, self.training_history, self.lock_event_repository
        )
        self.check_setup_complete_service = CheckSetupCompleteService(self.profile_repository)
        prepare_desktop = PrepareDesktopForLockService(
            self.desktop_to_restore_repository, self.desktop, self.music_player, self.warning_popup
        )
        enter_lock = EnterLockService(
            self.lock_runtime,
            prepare_desktop,
            self.lock_event_repository,
            self.begin_training_session_service,
            self.profile_repository,
            self.settings_state,
        )
        self.leave_lock_service = LeaveLockService(
            self.lock_runtime,
            self.lock_event_repository,
            self.close_training_session_service,
            self.desktop_to_restore_repository,
            self.desktop,
            self.music_player,
        )
        self.run_lock_tick_service = RunLockTickService(
            SettleOverrideService(self.lock_runtime, self.override_repository, self.lock_event_repository),
            self.get_schedule_decision_service,
            ChooseLockService(
                self.lock_runtime,
                self.manual_lock_repository,
                self.lock_event_repository,
                self.plan_scheduled_lock_service,
                self.check_setup_complete_service,
                self.settings_state,
            ),
            WarnOfNextLockService(
                self.settings_state,
                self.given_warning_repository,
                self.lock_plan_repository,
                self.desktop,
                ShowWarningPopupService(
                    self.coach_plan_freshness, self.pending_replacements_service, self.desktop, self.warning_popup
                ),
            ),
            SwitchLockService(self.lock_runtime, enter_lock, self.leave_lock_service, self.lock_screen),
        )
        self.start_test_lock_service = StartTestLockService(self.lock_runtime)
        self.start_manual_lock_service = StartManualLockService(self.lock_runtime, self.manual_lock_repository)
        self.request_override_service = RequestOverrideService(
            self.lock_runtime, self.override_repository, self.settings_state
        )
        self.cancel_override_service = CancelOverrideService(self.lock_runtime, self.override_repository)
        self.end_lock_on_session_finished_service = EndLockOnSessionFinishedService(
            self.lock_runtime, self.lock_event_repository, self.catalogue, self.desktop
        )

    def _build_coach_services(self) -> None:
        build_context = BuildCoachContextService(
            self.profile_repository,
            self.training_history,
            self.lock_event_repository,
            self.catalogue,
            RecallCoachMemoryService(
                self.coach_memory_repository,
                self.memory_note_version_repository,
                self.replacement_answer_repository,
                self.catalogue,
            ),
            DescribeLaddersForCoachService(
                self.ladder_position_repository, self.ladder_move_record_repository, self.catalogue
            ),
        )
        store_plan = StoreCoachPlanService(
            ApplyLadderOverridesService(self.ladder_position_repository, self.ladder_move_record_repository),
            self.training_history,
            self.coach_memory_repository,
            self.coach_plan_repository,
            self.coach_review_repository,
            self.clock,
        )
        self.run_coach_service = RunCoachService(
            self.coach,
            self.catalogue,
            PrepareCoachRunService(self.clock, self.coach_plan_freshness, build_context),
            LogCoachRunService(self.coach_run_repository, self.clock),
            store_plan,
            self.desktop,
        )
        records = TrainingRecords(
            self.training_history,
            self.ladder_position_repository,
            self.ladder_move_record_repository,
            self.lock_event_repository,
            self.catalogue,
            self.clock,
        )
        self.build_report_service = BuildReportService(
            records, self.profile_repository, self.coach_memory_repository, self.coach_review_repository, self.clock
        )
        self.write_coach_review_service = WriteCoachReviewService(
            self.coach, build_context, records, self.coach_review_repository, self.training_history, self.clock
        )
        self.get_memory_service = GetMemoryService(self.coach_memory_repository)
        self.forget_memory_note_service = ForgetMemoryNoteService(self.coach_memory_repository, self.clock)
        self.build_calendar_service = BuildCalendarService(
            self.training_history,
            self.lock_event_repository,
            self.lock_plan_repository,
            self.coach_plan_freshness,
            self.catalogue,
        )

    def _build_music_services(self) -> None:
        self.control_music_service = ControlMusicService(self.music_player)
        self.music_snapshot_service = MusicSnapshotService(self.music_player, self.music_library, self.settings_state)
        self.connect_music_account_service = ConnectMusicAccountService(self.music_account)
        self.disconnect_music_account_service = DisconnectMusicAccountService(self.music_account)
        self.get_music_account_service = GetMusicAccountService(self.music_account)

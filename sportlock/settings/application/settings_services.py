"""The settings in force, and their changes.

Edits made while a lock is active or due within its 10-minute warning only apply once that lock is over.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sportlock.locks.domain.schedule import Decision, settings_frozen
from sportlock.settings.domain.repositories import SettingsChangeMarkerProtocol, SettingsRepositoryProtocol
from sportlock.settings.domain.settings import Settings, SettingsError
from sportlock.shared_kernel.ports import DesktopProtocol

log = logging.getLogger("sportlock")


class SettingsState:
    """The settings the service runs with (in memory), plus what is waiting to be applied."""

    def __init__(self) -> None:
        self.settings = Settings()
        self.error: str | None = None
        self.pending = False
        self.marker = -1.0  # change marker of the file the settings were read from


class ReloadSettingsService:
    """Applies a changed config file, unless a lock freezes the settings."""

    def __init__(
        self,
        settings_state: SettingsState,
        settings_repository: SettingsRepositoryProtocol,
        settings_change_marker: SettingsChangeMarkerProtocol,
        desktop: DesktopProtocol,
    ) -> None:
        self.settings_state = settings_state
        self.settings_repository = settings_repository
        self.settings_change_marker = settings_change_marker
        self.desktop = desktop

    def execute(self, *, decision: Decision | None, now: datetime, force: bool = False) -> None:
        """`decision`: what the schedule says now (None skips the freeze check)."""
        changed = self.settings_change_marker.marker() != self.settings_state.marker
        if not force and not changed and not self.settings_state.pending:
            return
        if not force and decision is not None and settings_frozen(decision, now):
            self.settings_state.pending = True
            return
        try:
            self.settings_state.settings = self.settings_repository.get()
            self.settings_state.error = None
        except SettingsError as error:
            if self.settings_state.error != str(error):
                self.desktop.notify("sportlock config error", str(error), urgent=True)
            self.settings_state.error = str(error)
        self.settings_state.marker = self.settings_change_marker.marker()
        self.settings_state.pending = False


class SaveSettingsService:
    """The settings form was saved."""

    def __init__(
        self,
        settings_state: SettingsState,
        settings_repository: SettingsRepositoryProtocol,
        reload_settings_service: ReloadSettingsService,
    ) -> None:
        self.settings_state = settings_state
        self.settings_repository = settings_repository
        self.reload_settings_service = reload_settings_service

    def execute(self, form: dict, *, decision: Decision, now: datetime) -> bool:
        """Validates and writes the file.

        Returns whether applying it waits for the current lock. Raises SettingsError.
        """
        self.settings_repository.update(self.settings_state.settings.with_form(form))
        self.reload_settings_service.execute(decision=decision, now=now)
        log.info(
            "settings saved from the app (%s)",
            "pending until the lock is over" if self.settings_state.pending else "applied",
        )
        return self.settings_state.pending


class GetSettingsService:
    """What the settings form shows: the file, which may be newer than what applies while frozen."""

    def __init__(self, settings_state: SettingsState, settings_repository: SettingsRepositoryProtocol) -> None:
        self.settings_state = settings_state
        self.settings_repository = settings_repository

    def execute(self) -> Settings:
        """The file's settings, or the ones in force when the file is invalid."""
        try:
            return self.settings_repository.get()
        except SettingsError:
            return self.settings_state.settings

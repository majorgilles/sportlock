"""The settings in force, and their changes. Edits made while a lock is active or due within its
10-minute warning only apply once that lock is over."""

from __future__ import annotations

import logging
from datetime import datetime

from sportlock.locks.domain.schedule import Decision, settings_frozen
from sportlock.settings.domain.repositories import SettingsRepositoryProtocol
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

    def __init__(self, state: SettingsState, repository: SettingsRepositoryProtocol, desktop: DesktopProtocol) -> None:
        self.state = state
        self.repository = repository
        self.desktop = desktop

    def execute(self, *, decision: Decision | None, now: datetime, force: bool = False) -> None:
        """`decision`: what the schedule says now (None skips the freeze check)."""
        changed = self.repository.marker() != self.state.marker
        if not force and not changed and not self.state.pending:
            return
        if not force and decision is not None and settings_frozen(decision, now):
            self.state.pending = True
            return
        try:
            self.state.settings = self.repository.load()
            self.state.error = None
        except SettingsError as error:
            if self.state.error != str(error):
                self.desktop.notify("sportlock config error", str(error), urgent=True)
            self.state.error = str(error)
        self.state.marker = self.repository.marker()
        self.state.pending = False


class SaveSettingsService:
    """The settings form was saved."""

    def __init__(self, state: SettingsState, repository: SettingsRepositoryProtocol, reload: ReloadSettingsService) -> None:
        self.state = state
        self.repository = repository
        self.reload = reload

    def execute(self, form: dict, *, decision: Decision, now: datetime) -> bool:
        """Validates and writes the file; returns whether applying it waits for the current lock.
        Raises SettingsError."""
        self.repository.save(self.state.settings.with_form(form))
        self.reload.execute(decision=decision, now=now)
        log.info("settings saved from the app (%s)", "pending until the lock is over" if self.state.pending else "applied")
        return self.state.pending


class GetSettingsService:
    """What the settings form shows: the file, which may be newer than what applies while frozen."""

    def __init__(self, state: SettingsState, repository: SettingsRepositoryProtocol) -> None:
        self.state = state
        self.repository = repository

    def execute(self) -> Settings:
        """The file's settings, or the ones in force when the file is invalid."""
        try:
            return self.repository.load()
        except SettingsError:
            return self.state.settings

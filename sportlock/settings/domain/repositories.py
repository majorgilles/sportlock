"""Persistence ports for the settings."""

from __future__ import annotations

from typing import Protocol

from sportlock.settings.domain.settings import Settings
from sportlock.shared_kernel.repositories import GetSingletonRepositoryProtocol, UpdateSingletonRepositoryProtocol


class SettingsRepositoryProtocol(
    GetSingletonRepositoryProtocol[Settings], UpdateSingletonRepositoryProtocol[Settings], Protocol
):
    """Where the settings live (config.toml).

    `get` never returns None: a missing file is created with the defaults. It raises SettingsError for
    settings that break a rule.
    """


class SettingsChangeMarkerProtocol(Protocol):
    """Tells whether the stored settings changed since they were last read."""

    def marker(self) -> float:
        """Changes whenever the stored settings change (e.g. the file's modification time)."""
        ...

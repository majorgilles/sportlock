"""Persistence port for the settings."""

from __future__ import annotations

from typing import Protocol

from sportlock.settings.domain.settings import Settings


class SettingsRepositoryProtocol(Protocol):
    """Where the settings live (config.toml)."""

    def marker(self) -> float:
        """Changes whenever the stored settings change (e.g. the file's modification time)."""
        ...

    def load(self) -> Settings:
        """Read and validate the settings. Raises SettingsError."""
        ...

    def save(self, settings: Settings) -> None:
        """Write the settings."""
        ...

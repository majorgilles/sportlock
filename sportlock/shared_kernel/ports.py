"""Ports to the desktop and the clock, used by several subdomains' use cases."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class ClockProtocol(Protocol):
    """Wall-clock time, to the second."""

    def now(self) -> datetime:
        """The current local time without microseconds."""
        ...


class DesktopProtocol(Protocol):
    """Side effects on the user's desktop."""

    def notify(self, headline: str, description: str = "", *, urgent: bool = False) -> None:
        """Show a notification."""
        ...

    def pause_media(self) -> list[str]:
        """Pause every playing media player; returns the ones paused."""
        ...

    def resume_media(self, players: list[str]) -> None:
        """Resume the given players."""
        ...

    def idle_stay_awake(self) -> bool | None:
        """Whether the desktop's "stay awake" toggle is on (None when unknown)."""
        ...

    def set_idle_stay_awake(self, stay_awake: bool) -> None:
        """Turn "stay awake" on or off."""
        ...

    def system_lock_active(self) -> bool:
        """Whether the desktop's own lock screen is up."""
        ...

    def theme(self) -> dict[str, str]:
        """Colours for the sportlock screens."""
        ...


class LockScreenProtocol(Protocol):
    """The full-screen lock the training happens on."""

    def ensure_shown(self) -> None:
        """Show it, or show it again if it closed while a lock is active."""
        ...


class WarningPopupProtocol(Protocol):
    """The popup in front of everything that announces an upcoming lock."""

    def show(self, content: dict) -> None:
        """Show it (replacing one already shown)."""
        ...

    def close(self) -> None:
        """Close it if it is still shown."""
        ...

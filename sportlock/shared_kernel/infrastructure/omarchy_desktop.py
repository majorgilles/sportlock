"""The Omarchy desktop: notifications, media players, idle and lock state, theme colours."""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path
from typing import override

from sportlock.shared_kernel.ports import DesktopProtocol

THEME_COLORS = Path.home() / ".local/state/omarchy/current/theme/colors.toml"


def _run(*command: str, timeout: float = 5) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except OSError, subprocess.TimeoutExpired:
        return None


def notify(headline: str, description: str = "", *, urgent: bool = False) -> None:
    """Show an Omarchy notification."""
    args = ["omarchy-notification-send", "--app-name", "sportlock", "-g", "󰖏"]
    if urgent:
        args += ["-u", "critical"]
    _run(*args, headline, description)


MPRIS_PATH = "/org/mpris/MediaPlayer2"
MPRIS_PLAYER = "org.mpris.MediaPlayer2.Player"


def _media_players() -> list[str]:
    result = _run("busctl", "--user", "list", "--no-legend", "--acquired")
    if not result:
        return []
    names = (line.split()[0] for line in result.stdout.splitlines() if line.strip())
    return [name for name in names if name.startswith("org.mpris.MediaPlayer2.")]


def pause_media() -> list[str]:
    """Pause every playing MPRIS player; return the ones paused so they can be resumed."""
    paused = []
    for name in _media_players():
        status = _run("busctl", "--user", "get-property", name, MPRIS_PATH, MPRIS_PLAYER, "PlaybackStatus")
        if status and '"Playing"' in status.stdout:
            _run("busctl", "--user", "call", name, MPRIS_PATH, MPRIS_PLAYER, "Pause")
            paused.append(name)
    return paused


def resume_media(names: list[str]) -> None:
    """Resume the given MPRIS players."""
    for name in names:
        _run("busctl", "--user", "call", name, MPRIS_PATH, MPRIS_PLAYER, "Play")


def idle_stay_awake() -> bool | None:
    """Omarchy's "stay awake" toggle; None when the shell can't be asked."""
    result = _run("omarchy-shell", "idle", "status")
    try:
        return bool(json.loads(result.stdout)["stayAwake"]) if result else None
    except ValueError, KeyError:
        return None


def set_idle_stay_awake(stay_awake: bool) -> None:
    """Turn Omarchy's stay-awake toggle on or off."""
    _run("omarchy-shell", "idle", "disable" if stay_awake else "enable")


def omarchy_lock_active() -> bool:
    """Whether Omarchy's own lock screen is up."""
    result = _run("omarchy-shell", "lock", "isLocked")
    return bool(result and result.stdout.strip() == "true")


def theme() -> dict[str, str]:
    """The current Omarchy theme's colours."""
    colors = {
        "background": "#121212",
        "foreground": "#bebebe",
        "accent": "#e68e0d",
        "muted": "#555555",
        "urgent": "#d35f5f",
    }
    try:
        data = tomllib.loads(THEME_COLORS.read_text())
    except OSError, tomllib.TOMLDecodeError:
        return colors
    for key in ("background", "foreground", "accent"):
        if isinstance(data.get(key), str):
            colors[key] = data[key]
    if isinstance(data.get("dark_foreground"), str):
        colors["muted"] = data["dark_foreground"]
    if isinstance(data.get("red"), str):
        colors["urgent"] = data["red"]
    return colors


class OmarchyDesktop(DesktopProtocol):
    """DesktopProtocol over the module functions above."""

    @override
    def notify(self, headline: str, description: str = "", *, urgent: bool = False) -> None:
        """Show an Omarchy notification."""
        notify(headline, description, urgent=urgent)

    @override
    def pause_media(self) -> list[str]:
        return pause_media()

    @override
    def resume_media(self, players: list[str]) -> None:
        """Resume the given MPRIS players."""
        resume_media(players)

    @override
    def idle_stay_awake(self) -> bool | None:
        return idle_stay_awake()

    @override
    def set_idle_stay_awake(self, stay_awake: bool) -> None:
        """Turn Omarchy's stay-awake toggle on or off."""
        set_idle_stay_awake(stay_awake)

    @override
    def system_lock_active(self) -> bool:
        return omarchy_lock_active()

    @override
    def theme(self) -> dict[str, str]:
        """The current Omarchy theme's colours."""
        return theme()

"""Side effects on the desktop: notifications, audio, Omarchy idle and lock state, theme."""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

THEME_COLORS = Path.home() / ".local/state/omarchy/current/theme/colors.toml"
SINK = "@DEFAULT_AUDIO_SINK@"


def _run(*command: str, timeout: float = 5) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def notify(headline: str, description: str = "", *, urgent: bool = False) -> None:
    args = ["omarchy-notification-send", "--app-name", "sportlock", "-g", "󰖏"]
    if urgent:
        args += ["-u", "critical"]
    _run(*args, headline, description)


def audio_muted() -> bool:
    result = _run("wpctl", "get-volume", SINK)
    return bool(result and "[MUTED]" in result.stdout)


def set_audio_muted(muted: bool) -> None:
    _run("wpctl", "set-mute", SINK, "1" if muted else "0")


def idle_stay_awake() -> bool | None:
    """Omarchy's "stay awake" toggle; None when the shell can't be asked."""
    result = _run("omarchy-shell", "idle", "status")
    try:
        return bool(json.loads(result.stdout)["stayAwake"]) if result else None
    except (ValueError, KeyError):
        return None


def set_idle_stay_awake(stay_awake: bool) -> None:
    _run("omarchy-shell", "idle", "disable" if stay_awake else "enable")


def omarchy_lock_active() -> bool:
    result = _run("omarchy-shell", "lock", "isLocked")
    return bool(result and result.stdout.strip() == "true")


def theme() -> dict[str, str]:
    colors = {"background": "#121212", "foreground": "#bebebe", "accent": "#e68e0d",
              "muted": "#555555", "urgent": "#d35f5f"}
    try:
        data = tomllib.loads(THEME_COLORS.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return colors
    for key in ("background", "foreground", "accent"):
        if isinstance(data.get(key), str):
            colors[key] = data[key]
    if isinstance(data.get("dark_foreground"), str):
        colors["muted"] = data["dark_foreground"]
    if isinstance(data.get("red"), str):
        colors["urgent"] = data["red"]
    return colors

"""config.toml: where the settings live (~/.config/sportlock/config.toml)."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import override

from sportlock.settings.domain.repositories import SettingsRepositoryProtocol
from sportlock.settings.domain.settings import DAYS, Settings, SettingsError

CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "sportlock" / "config.toml"

DEFAULT_CONFIG = """\
# sportlock configuration. Changes made during a lock or its 10-minute warning
# only apply once that lock is over.

[general]
enabled = false            # set to true once your schedule below is right
max_minutes_per_day = 60   # total lock time per day, all locks combined
warn_minutes = [10, 2]     # notifications before a lock starts
warn_popup = true          # the first warning is also a popup in front of everything

[training]
lead_in_seconds = 5        # get-ready countdown after pressing Start set (0 = none)

[recovery]
allow_rest_days = true     # the coach may turn a lock into a rest day after a big session
max_rest_days_in_a_row = 2
min_sessions_per_week = 3  # no rest day unless you trained at least this often in the last 7 days
recovery_minutes = 15      # default length of a recovery lock

[override]
phrase = "I am choosing to skip my training today"
wait_seconds = 300

[profile]
# What you can train with. Household furniture is assumed; add "bar" (pull-up bar),
# "dip-bars", "parallettes" or "anchor" (something to hook your feet under) when you have them.
equipment = ["chair", "table", "bench", "doorway"]

[notebook]
id = "876228fa-5c2b-4ced-8d81-ee0de4d7e93a"   # NotebookLM notebook the exercise library is built from

# One block per scheduled lock.
[[lock]]
days = ["mon", "tue", "wed", "thu", "fri"]
at = "18:00"
minutes = 30
"""


class TomlSettingsRepository(SettingsRepositoryProtocol):
    """Reads and writes config.toml; a missing file is created from DEFAULT_CONFIG."""

    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self.path = path

    def _mtime(self) -> float:
        try:
            return self.path.stat().st_mtime if self.path.exists() else 0.0
        except OSError:
            return 0.0

    @override
    def load(self) -> Settings:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(DEFAULT_CONFIG)
        try:
            return Settings.parse(tomllib.loads(self.path.read_text()))
        except tomllib.TOMLDecodeError as error:
            raise SettingsError(f"{self.path}: {error}") from None

    @override
    def save(self, settings: Settings) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(dump(settings))
        tmp.replace(self.path)

    @override
    def marker(self) -> float:
        return self._mtime()


def _toml_list(values) -> str:
    return "[" + ", ".join(f'"{v}"' if isinstance(v, str) else str(v) for v in values) + "]"


def dump(config: Settings) -> str:
    """Write the config back as commented TOML (used by the settings form)."""
    phrase = config.override_phrase.replace("\\", "\\\\").replace('"', '\\"')
    lines = [
        "# sportlock configuration (also editable in `sportlock app` → Schedule & settings).",
        "# Changes made during a lock or its 10-minute warning only apply once that lock is over.",
        "",
        "[general]",
        f"enabled = {'true' if config.enabled else 'false'}",
        f"max_minutes_per_day = {config.max_minutes_per_day}   # total lock time per day, all locks combined",
        f"warn_minutes = {_toml_list(config.warn_minutes)}     # notifications before a lock starts",
        f"warn_popup = {'true' if config.warn_popup else 'false'}          # the first warning is also a popup in front of everything",
        "",
        "[training]",
        f"lead_in_seconds = {config.lead_in_seconds}        # get-ready countdown after pressing Start set (0 = none)",
        "",
        "[recovery]",
        f"allow_rest_days = {'true' if config.allow_rest_days else 'false'}",
        f"max_rest_days_in_a_row = {config.max_rest_days_in_a_row}",
        f"min_sessions_per_week = {config.min_sessions_per_week}",
        f"recovery_minutes = {config.recovery_minutes}",
        "",
        "[override]",
        f'phrase = "{phrase}"',
        f"wait_seconds = {config.override_wait_seconds}",
        "",
        "[profile]",
        "# Only used until a profile is saved in `sportlock app`, which takes precedence.",
        f"equipment = {_toml_list(sorted(config.equipment))}",
        "",
        "[notebook]",
        f'id = "{config.notebook_id}"',
        "",
        "# One block per scheduled lock.",
    ]
    for lock in config.locks:
        lines += [
            "[[lock]]",
            f"days = {_toml_list([DAYS[d] for d in sorted(lock.days)])}",
            f'at = "{lock.at.strftime("%H:%M")}"',
            f"minutes = {lock.minutes}",
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"

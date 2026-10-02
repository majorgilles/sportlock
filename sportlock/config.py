"""Load and validate ~/.config/sportlock/config.toml."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "sportlock" / "config.toml"

DEFAULT_CONFIG = """\
# sportlock configuration. Changes made during a lock or its 10-minute warning
# only apply once that lock is over.

[general]
enabled = false            # set to true once your schedule below is right
max_minutes_per_day = 60   # total lock time per day, all locks combined
warn_minutes = [10, 2]     # notifications before a lock starts

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


DEFAULT_NOTEBOOK = "876228fa-5c2b-4ced-8d81-ee0de4d7e93a"
DEFAULT_EQUIPMENT = frozenset({"chair", "table", "bench", "doorway"})


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class LockEntry:
    days: frozenset[int]  # 0 = Monday
    at: time
    minutes: int


@dataclass(frozen=True)
class Config:
    enabled: bool = True
    max_minutes_per_day: int = 60
    warn_minutes: tuple[int, ...] = (10, 2)
    override_phrase: str = "I am choosing to skip my training today"
    override_wait_seconds: int = 300
    locks: tuple[LockEntry, ...] = field(default_factory=tuple)
    notebook_id: str = DEFAULT_NOTEBOOK
    equipment: frozenset[str] = DEFAULT_EQUIPMENT


def _parse_time(value: object, where: str) -> time:
    if not isinstance(value, str):
        raise ConfigError(f"{where}: 'at' must be a string like \"18:00\"")
    try:
        hours, minutes = value.split(":")
        return time(int(hours), int(minutes))
    except ValueError:
        raise ConfigError(f"{where}: invalid time {value!r}, expected HH:MM") from None


def _parse_days(value: object, where: str) -> frozenset[int]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{where}: 'days' must be a non-empty list like [\"mon\", \"wed\"]")
    days = set()
    for day in value:
        if not isinstance(day, str) or day.lower()[:3] not in DAYS:
            raise ConfigError(f"{where}: unknown day {day!r}")
        days.add(DAYS.index(day.lower()[:3]))
    return frozenset(days)


def _positive_int(value: object, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{where} must be a positive integer")
    return value


def parse(data: dict) -> Config:
    general = data.get("general", {})
    override = data.get("override", {})
    notebook = data.get("notebook", {})
    profile = data.get("profile", {})
    equipment = profile.get("equipment", sorted(DEFAULT_EQUIPMENT))
    if not isinstance(equipment, list) or not all(isinstance(e, str) for e in equipment):
        raise ConfigError("profile.equipment must be a list of strings")

    locks = []
    for index, entry in enumerate(data.get("lock", [])):
        where = f"lock #{index + 1}"
        locks.append(
            LockEntry(
                days=_parse_days(entry.get("days"), where),
                at=_parse_time(entry.get("at"), where),
                minutes=_positive_int(entry.get("minutes"), f"{where}: 'minutes'"),
            )
        )

    warn = general.get("warn_minutes", [10, 2])
    if not isinstance(warn, list) or not all(isinstance(m, int) and m > 0 for m in warn):
        raise ConfigError("general.warn_minutes must be a list of positive integers")

    phrase = override.get("phrase", Config.override_phrase)
    if not isinstance(phrase, str) or len(phrase.strip()) < 10:
        raise ConfigError("override.phrase must be at least 10 characters")

    return Config(
        enabled=bool(general.get("enabled", True)),
        max_minutes_per_day=_positive_int(general.get("max_minutes_per_day", 60), "general.max_minutes_per_day"),
        warn_minutes=tuple(sorted(set(warn), reverse=True)),
        override_phrase=phrase.strip(),
        override_wait_seconds=_positive_int(override.get("wait_seconds", 300), "override.wait_seconds"),
        locks=tuple(locks),
        notebook_id=str(notebook.get("id") or DEFAULT_NOTEBOOK),
        equipment=frozenset(e.strip().lower() for e in equipment),
    )


def load(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CONFIG)
    try:
        return parse(tomllib.loads(path.read_text()))
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path}: {error}") from None

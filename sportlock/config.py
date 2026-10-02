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

[training]
lead_in_seconds = 5        # get-ready countdown after pressing Start set (0 = none)

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
    lead_in_seconds: int = 5


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
    lead_in = data.get("training", {}).get("lead_in_seconds", 5)
    if not isinstance(lead_in, int) or isinstance(lead_in, bool) or not 0 <= lead_in <= 30:
        raise ConfigError("training.lead_in_seconds must be a whole number from 0 to 30")
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
        lead_in_seconds=lead_in,
    )


def load(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CONFIG)
    try:
        return parse(tomllib.loads(path.read_text()))
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path}: {error}") from None


# -- the settings form (sportlock app) ---------------------------------------------------------


def to_settings(config: Config) -> dict:
    """The form's view of the config (JSON-friendly)."""
    return {
        "enabled": config.enabled,
        "max_minutes_per_day": config.max_minutes_per_day,
        "warn_minutes": list(config.warn_minutes),
        "lead_in_seconds": config.lead_in_seconds,
        "override_phrase": config.override_phrase,
        "override_wait_seconds": config.override_wait_seconds,
        "locks": [{"days": [DAYS[d] for d in sorted(lock.days)], "at": lock.at.strftime("%H:%M"),
                   "minutes": lock.minutes} for lock in config.locks],
    }


def from_settings(settings: dict, current: Config) -> Config:
    """Validate the form's values (same rules as the file) and keep what the form doesn't edit."""
    def number(key, default):
        value = settings.get(key, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ConfigError(f"{key.replace('_', ' ')} must be a whole number") from None

    locks = []
    for index, lock in enumerate(settings.get("locks", [])):
        minutes = number_in(lock.get("minutes"), f"lock #{index + 1} minutes")
        if minutes > 240:
            raise ConfigError(f"lock #{index + 1}: at most 240 minutes")
        locks.append({"days": lock.get("days") or [], "at": str(lock.get("at", "")).strip(), "minutes": minutes})

    data = {
        "general": {"enabled": bool(settings.get("enabled")),
                    "max_minutes_per_day": number("max_minutes_per_day", current.max_minutes_per_day),
                    "warn_minutes": [number_in(m, "warning minutes") for m in settings.get("warn_minutes", [])]},
        "training": {"lead_in_seconds": number("lead_in_seconds", current.lead_in_seconds)},
        "override": {"phrase": str(settings.get("override_phrase", current.override_phrase)),
                     "wait_seconds": number("override_wait_seconds", current.override_wait_seconds)},
        "profile": {"equipment": sorted(current.equipment)},
        "notebook": {"id": current.notebook_id},
        "lock": locks,
    }
    return parse(data)


def number_in(value: object, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{label} must be a whole number") from None


def _toml_list(values) -> str:
    return "[" + ", ".join(f'"{v}"' if isinstance(v, str) else str(v) for v in values) + "]"


def dump(config: Config) -> str:
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
        "",
        "[training]",
        f"lead_in_seconds = {config.lead_in_seconds}        # get-ready countdown after pressing Start set (0 = none)",
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
        lines += ["[[lock]]", f"days = {_toml_list([DAYS[d] for d in sorted(lock.days)])}",
                  f'at = "{lock.at.strftime("%H:%M")}"', f"minutes = {lock.minutes}", ""]
    return "\n".join(lines).rstrip() + "\n"

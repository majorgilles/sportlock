"""The user's settings: lock schedule, limits, recovery guardrails, override, defaults.

Pure validation of the values; reading and writing config.toml is the infrastructure's job
(`infrastructure/config_toml.py`).
"""

from __future__ import annotations

from datetime import time

from sportlock.shared_kernel.base import ValueObject
from sportlock.shared_kernel.errors import DomainError

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_NOTEBOOK = "876228fa-5c2b-4ced-8d81-ee0de4d7e93a"
DEFAULT_EQUIPMENT = frozenset({"chair", "table", "bench", "doorway"})
DEFAULT_OVERRIDE_PHRASE = "I am choosing to skip my training today"


class SettingsError(DomainError):
    """A setting has an invalid value."""


class LockEntry(ValueObject):
    """One scheduled lock: on these weekdays (0 = Monday), at this time, for this long."""

    days: frozenset[int]
    at: time
    minutes: int


class Settings(ValueObject):
    """Everything config.toml holds."""

    enabled: bool = True
    max_minutes_per_day: int = 60
    warn_minutes: tuple[int, ...] = (10, 2)
    warn_popup: bool = True
    override_phrase: str = DEFAULT_OVERRIDE_PHRASE
    override_wait_seconds: int = 300
    locks: tuple[LockEntry, ...] = ()
    notebook_id: str = DEFAULT_NOTEBOOK
    equipment: frozenset[str] = DEFAULT_EQUIPMENT
    lead_in_seconds: int = 5
    allow_rest_days: bool = True
    max_rest_days_in_a_row: int = 2
    min_sessions_per_week: int = 3
    recovery_minutes: int = 15

    @classmethod
    def parse(cls, data: dict) -> Settings:
        """Validate the config file's tables ([general], [lock], …)."""
        general, override = data.get("general", {}), data.get("override", {})
        notebook, profile, rec = data.get("notebook", {}), data.get("profile", {}), data.get("recovery", {})

        def ranged(key: str, default: int, lo: int, hi: int) -> int:
            value = rec.get(key, default)
            if not _is_int(value) or not lo <= value <= hi:
                raise SettingsError(f"recovery.{key} must be a whole number from {lo} to {hi}")
            return value

        lead_in = data.get("training", {}).get("lead_in_seconds", 5)
        if not _is_int(lead_in) or not 0 <= lead_in <= 30:
            raise SettingsError("training.lead_in_seconds must be a whole number from 0 to 30")
        equipment = profile.get("equipment", sorted(DEFAULT_EQUIPMENT))
        if not isinstance(equipment, list) or not all(isinstance(e, str) for e in equipment):
            raise SettingsError("profile.equipment must be a list of strings")
        locks = []
        for index, entry in enumerate(data.get("lock", [])):
            where = f"lock #{index + 1}"
            locks.append(LockEntry(days=_parse_days(entry.get("days"), where), at=_parse_time(entry.get("at"), where),
                                   minutes=_positive_int(entry.get("minutes"), f"{where}: 'minutes'")))
        warn = general.get("warn_minutes", [10, 2])
        if not isinstance(warn, list) or not all(isinstance(m, int) and m > 0 for m in warn):
            raise SettingsError("general.warn_minutes must be a list of positive integers")
        phrase = override.get("phrase", DEFAULT_OVERRIDE_PHRASE)
        if not isinstance(phrase, str) or len(phrase.strip()) < 10:
            raise SettingsError("override.phrase must be at least 10 characters")
        return cls(
            enabled=bool(general.get("enabled", True)),
            max_minutes_per_day=_positive_int(general.get("max_minutes_per_day", 60), "general.max_minutes_per_day"),
            warn_minutes=tuple(sorted(set(warn), reverse=True)), warn_popup=bool(general.get("warn_popup", True)),
            override_phrase=phrase.strip(),
            override_wait_seconds=_positive_int(override.get("wait_seconds", 300), "override.wait_seconds"),
            locks=tuple(locks), notebook_id=str(notebook.get("id") or DEFAULT_NOTEBOOK),
            equipment=frozenset(e.strip().lower() for e in equipment), lead_in_seconds=lead_in,
            allow_rest_days=bool(rec.get("allow_rest_days", True)),
            max_rest_days_in_a_row=ranged("max_rest_days_in_a_row", 2, 0, 6),
            min_sessions_per_week=ranged("min_sessions_per_week", 3, 0, 7),
            recovery_minutes=ranged("recovery_minutes", 15, 5, 60),
        )

    def to_form(self) -> dict:
        """The settings form's JSON view."""
        return {
            "enabled": self.enabled, "max_minutes_per_day": self.max_minutes_per_day,
            "warn_minutes": list(self.warn_minutes), "warn_popup": self.warn_popup,
            "lead_in_seconds": self.lead_in_seconds, "override_phrase": self.override_phrase,
            "override_wait_seconds": self.override_wait_seconds, "allow_rest_days": self.allow_rest_days,
            "max_rest_days_in_a_row": self.max_rest_days_in_a_row, "min_sessions_per_week": self.min_sessions_per_week,
            "recovery_minutes": self.recovery_minutes,
            "locks": [{"days": [DAYS[d] for d in sorted(lock.days)], "at": lock.at.strftime("%H:%M"),
                       "minutes": lock.minutes} for lock in self.locks],
        }

    def with_form(self, form: dict) -> Settings:
        """Validate the form's values (same rules as the file), keeping what the form doesn't edit."""
        def number(key: str, default: int) -> int:
            return _whole(form.get(key, default), key.replace("_", " "))

        locks = []
        for index, lock in enumerate(form.get("locks", [])):
            minutes = _whole(lock.get("minutes"), f"lock #{index + 1} minutes")
            if minutes > 240:
                raise SettingsError(f"lock #{index + 1}: at most 240 minutes")
            locks.append({"days": lock.get("days") or [], "at": str(lock.get("at", "")).strip(), "minutes": minutes})
        return Settings.parse({
            "general": {"enabled": bool(form.get("enabled")),
                        "max_minutes_per_day": number("max_minutes_per_day", self.max_minutes_per_day),
                        "warn_minutes": [_whole(m, "warning minutes") for m in form.get("warn_minutes", [])],
                        "warn_popup": bool(form.get("warn_popup", self.warn_popup))},
            "training": {"lead_in_seconds": number("lead_in_seconds", self.lead_in_seconds)},
            "override": {"phrase": str(form.get("override_phrase", self.override_phrase)),
                         "wait_seconds": number("override_wait_seconds", self.override_wait_seconds)},
            "recovery": {"allow_rest_days": bool(form.get("allow_rest_days", self.allow_rest_days)),
                         "max_rest_days_in_a_row": number("max_rest_days_in_a_row", self.max_rest_days_in_a_row),
                         "min_sessions_per_week": number("min_sessions_per_week", self.min_sessions_per_week),
                         "recovery_minutes": number("recovery_minutes", self.recovery_minutes)},
            "profile": {"equipment": sorted(self.equipment)},
            "notebook": {"id": self.notebook_id},
            "lock": locks,
        })


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _whole(value: object, label: str) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise SettingsError(f"{label} must be a whole number") from None


def _positive_int(value: object, where: str) -> int:
    if not _is_int(value) or value <= 0:  # type: ignore[operator]
        raise SettingsError(f"{where} must be a positive integer")
    return value  # type: ignore[return-value]


def _parse_time(value: object, where: str) -> time:
    if not isinstance(value, str):
        raise SettingsError(f"{where}: 'at' must be a string like \"18:00\"")
    try:
        hours, minutes = value.split(":")
        return time(int(hours), int(minutes))
    except ValueError:
        raise SettingsError(f"{where}: invalid time {value!r}, expected HH:MM") from None


def _parse_days(value: object, where: str) -> frozenset[int]:
    if not isinstance(value, list) or not value:
        raise SettingsError(f"{where}: 'days' must be a non-empty list like [\"mon\", \"wed\"]")
    days = set()
    for day in value:
        if not isinstance(day, str) or day.lower()[:3] not in DAYS:
            raise SettingsError(f"{where}: unknown day {day!r}")
        days.add(DAYS.index(day.lower()[:3]))
    return frozenset(days)

"""The athlete's profile from onboarding: experience, goals, equipment, limitations."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from sportlock.shared_kernel.base import ValueObject
from sportlock.shared_kernel.errors import DomainError
from sportlock.shared_kernel.time import iso

EXPERIENCE = ("beginner", "intermediate", "advanced")
GOALS = ("strength", "muscle", "endurance", "general fitness", "fat loss", "mobility")
EQUIPMENT = ("chair", "table", "bench", "doorway", "bar", "dip-bars", "parallettes", "rings", "bands", "anchor")
LOCATIONS = ("home, at the desktop", "elsewhere")


class ProfileError(DomainError):
    """The profile form has an invalid value."""


class Profile(ValueObject):
    """What the coach knows about the athlete from the profile form."""

    experience: str
    goals: tuple[str, ...]
    equipment: tuple[str, ...]
    location: str
    years_training: float | None = None
    injuries: str = ""
    age: int | None = None
    sex: str | None = None
    weight_kg: float | None = None
    updated_at: str = ""

    @classmethod
    def from_form(cls, raw: dict, now: datetime) -> Profile:
        """Validate the profile form."""
        experience = raw.get("experience")
        if experience not in EXPERIENCE:
            raise ProfileError("pick your experience level")
        goals = tuple(g for g in raw.get("goals", []) if g in GOALS)
        if not goals:
            raise ProfileError("pick at least one goal")

        def number(key: str, lo: float, hi: float, cast=int):
            value = raw.get(key)
            if value in (None, ""):
                return None
            try:
                value = cast(value)
            except (TypeError, ValueError):
                raise ProfileError(f"{key} must be a number") from None
            if not lo <= value <= hi:
                raise ProfileError(f"{key} must be between {lo} and {hi}")
            return value

        return cls(
            experience=experience, goals=goals,
            equipment=tuple(sorted({e for e in raw.get("equipment", []) if e in EQUIPMENT})),
            location=raw.get("location") if raw.get("location") in LOCATIONS else LOCATIONS[0],
            years_training=number("years_training", 0, 60, float), injuries=str(raw.get("injuries", "")).strip()[:2000],
            age=number("age", 10, 100), sex=str(raw.get("sex", "")).strip()[:20] or None,
            weight_kg=number("weight_kg", 25, 300, float), updated_at=iso(now),
        )

    @classmethod
    def from_dict(cls, data: dict) -> Profile:
        """Read the stored JSON shape."""
        return cls(experience=data["experience"], goals=data.get("goals", []), equipment=data.get("equipment", []),
                   location=data.get("location") or LOCATIONS[0], years_training=data.get("years_training"),
                   injuries=data.get("injuries") or "", age=data.get("age"), sex=data.get("sex"),
                   weight_kg=data.get("weight_kg"), updated_at=data.get("updated_at", ""))

    def to_dict(self) -> dict:
        """The stored JSON shape (lists, not tuples)."""
        return self.model_dump(mode="json")


class ProfileRepositoryProtocol(Protocol):
    """The one profile."""

    def get(self) -> Profile | None:
        """The saved profile, or None before onboarding."""
        ...

    def save(self, profile: Profile) -> None:
        """Replace the profile."""
        ...

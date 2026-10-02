"""The user profile from onboarding, and the starting ladder positions it implies."""

from __future__ import annotations

import json
from datetime import datetime

from .ladders import START
from .store import Store, _iso

PROFILE_KEY = "profile"
EXPERIENCE = ("beginner", "intermediate", "advanced")
GOALS = ("strength", "muscle", "endurance", "general fitness", "fat loss", "mobility")
EQUIPMENT = ("chair", "table", "bench", "doorway", "bar", "dip-bars", "parallettes", "rings", "bands", "anchor")
LOCATIONS = ("home, at the desktop", "elsewhere")

# Starting positions above beginner level: (exercise, target). Beginners use ladders.START.
PRESETS = {
    "intermediate": {
        "push-horizontal": ("push-up", {"sets": 3, "reps": [8, 12], "rest": 60}),
        "push-vertical": ("pike-push-up", {"sets": 3, "reps": [6, 10], "rest": 90}),
        "dip": ("bench-dip", {"sets": 3, "reps": [10, 15], "rest": 60}),
        "pull-horizontal": ("table-inverted-row", {"sets": 3, "reps": [8, 12], "rest": 60}),
        "pull-vertical": ("negative-pull-up", {"sets": 3, "reps": [3, 5], "rest": 120}),
        "squat": ("split-squat", {"sets": 3, "reps": [8, 12], "rest": 60}),
        "hinge": ("single-leg-glute-bridge", {"sets": 3, "reps": [8, 12], "rest": 45}),
        "core": ("plank", {"sets": 3, "seconds": 45, "rest": 45}),
    },
    "advanced": {
        "push-horizontal": ("diamond-push-up", {"sets": 4, "reps": [8, 12], "rest": 90}),
        "push-vertical": ("elevated-pike-push-up", {"sets": 4, "reps": [6, 10], "rest": 120}),
        "dip": ("parallel-bar-dip", {"sets": 4, "reps": [6, 10], "rest": 120}),
        "pull-horizontal": ("feet-elevated-inverted-row", {"sets": 4, "reps": [8, 12], "rest": 90}),
        "pull-vertical": ("pull-up", {"sets": 4, "reps": [5, 8], "rest": 150}),
        "squat": ("bulgarian-split-squat", {"sets": 4, "reps": [8, 12], "rest": 90}),
        "hinge": ("hamstring-walkout", {"sets": 3, "reps": [6, 10], "rest": 90}),
        "core": ("hollow-body-hold", {"sets": 3, "seconds": 40, "rest": 60}),
    },
}


class ProfileError(ValueError):
    pass


def validate(raw: dict) -> dict:
    experience = raw.get("experience")
    if experience not in EXPERIENCE:
        raise ProfileError("pick your experience level")
    goals = [g for g in raw.get("goals", []) if g in GOALS]
    if not goals:
        raise ProfileError("pick at least one goal")
    equipment = sorted({e for e in raw.get("equipment", []) if e in EQUIPMENT})

    def number(key, lo, hi, cast=int):
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

    location = raw.get("location") if raw.get("location") in LOCATIONS else LOCATIONS[0]
    return {
        "experience": experience,
        "years_training": number("years_training", 0, 60, float),
        "goals": goals,
        "equipment": equipment,
        "injuries": str(raw.get("injuries", "")).strip()[:2000],
        "age": number("age", 10, 100),
        "sex": str(raw.get("sex", "")).strip()[:20] or None,
        "weight_kg": number("weight_kg", 25, 300, float),
        "location": location,
    }


def save(store: Store, raw: dict, now: datetime) -> dict:
    """Validate and store the profile. First time only: seed ladders from the experience level."""
    profile = validate(raw)
    profile["updated_at"] = _iso(now)
    first_time = store.get(PROFILE_KEY) is None
    store.put(PROFILE_KEY, profile)

    has_progress = store.db.execute("SELECT COUNT(*) FROM ladders").fetchone()[0] > 0
    preset = PRESETS.get(profile["experience"])
    if first_time and preset and not has_progress:
        for chain in START:
            exercise, target = preset[chain]
            store.db.execute(
                "INSERT OR REPLACE INTO ladders (chain, exercise, target, reason, updated_at) VALUES (?, ?, ?, ?, ?)",
                (chain, exercise, json.dumps(target), f"Starting point for {profile['experience']} level", _iso(now)),
            )
    return profile


def load(store: Store) -> dict | None:
    return store.get(PROFILE_KEY)


def equipment(store: Store, default: frozenset[str]) -> set[str]:
    profile = load(store)
    return set(profile["equipment"]) if profile else set(default)

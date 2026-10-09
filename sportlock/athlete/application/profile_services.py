"""The athlete fills in or edits their profile."""

from __future__ import annotations

from datetime import datetime

from sportlock.athlete.domain.profile import Profile, ProfileRepositoryProtocol
from sportlock.progression.domain.ladders import STARTING_POINTS, LadderPosition, LadderRepositoryProtocol
from sportlock.settings.domain.settings import Settings


class SaveProfileService:
    """Validates and stores the profile. The first time only, an experienced athlete's ladders
    start above the beginner positions."""

    def __init__(self, profiles: ProfileRepositoryProtocol, ladders: LadderRepositoryProtocol) -> None:
        self.profiles = profiles
        self.ladders = ladders

    def execute(self, form: dict, now: datetime) -> Profile:
        """Raises ProfileError."""
        profile = Profile.from_form(form, now)
        first_time = self.profiles.get() is None
        self.profiles.save(profile)
        starting_points = STARTING_POINTS.get(profile.experience)
        if first_time and starting_points and not self.ladders.saved():
            for chain, (exercise, target) in starting_points.items():
                self.ladders.set(
                    LadderPosition(
                        chain=chain,
                        exercise=exercise,
                        target=target,
                        reason=f"Starting point for {profile.experience} level",
                    ),
                    now,
                )
        return profile


def equipment_of(profiles: ProfileRepositoryProtocol, settings: Settings) -> frozenset[str]:
    """What the athlete trains with: the profile's equipment, else the config default."""
    profile = profiles.get()
    return frozenset(profile.equipment) if profile else settings.equipment

"""SQLite adapter for the athlete profile (kept in the key-value table)."""

from __future__ import annotations

from typing import override

from sportlock.athlete.domain.profile import Profile, ProfileRepositoryProtocol
from sportlock.shared_kernel.infrastructure.database import Database

PROFILE_KEY = "profile"


class KvProfileRepository(ProfileRepositoryProtocol):
    """The profile as one JSON value."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @override
    def get(self) -> Profile | None:
        data = self.database.get(PROFILE_KEY)
        return Profile.from_dict(data) if data else None

    @override
    def save(self, profile: Profile) -> None:
        self.database.put(PROFILE_KEY, profile.to_dict())

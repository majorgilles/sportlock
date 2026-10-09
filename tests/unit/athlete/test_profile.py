from datetime import datetime

import pytest

from sportlock.athlete.domain.profile import Profile, ProfileError

NOW = datetime(2026, 10, 5, 18, 0)


def test_from_form__no_goal__rejected():
    with pytest.raises(ProfileError):
        Profile.from_form({"experience": "beginner", "goals": []}, NOW)


def test_from_form__age_not_a_number__rejected():
    with pytest.raises(ProfileError):
        Profile.from_form({"experience": "beginner", "goals": ["strength"], "age": "abc"}, NOW)


def test_from_form__unknown_goals_and_equipment__ignored():
    profile = Profile.from_form(
        {"experience": "beginner", "goals": ["strength", "flying"], "equipment": ["bar", "jetpack"]}, NOW
    )
    assert (profile.goals, profile.equipment) == (("strength",), ("bar",))


def test_profile__stored_shape__round_trips():
    profile = Profile.from_form(
        {
            "experience": "beginner",
            "goals": ["strength"],
            "equipment": ["chair"],
            "weight_kg": "78.5",
            "injuries": "right shoulder",
        },
        NOW,
    )
    assert Profile.from_dict(profile.to_dict()) == profile
    assert profile.to_dict()["goals"] == ["strength"]

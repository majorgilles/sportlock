import tomllib
from datetime import time

import pytest

from sportlock.settings.domain.settings import Settings, SettingsError
from sportlock.settings.infrastructure.config_toml import DEFAULT_CONFIG, dump


def test_parse__lock_days_and_time__read():
    settings = Settings.parse({"lock": [{"days": ["mon", "Friday"], "at": "07:30", "minutes": 20}]})
    assert (settings.locks[0].days, settings.locks[0].at) == (frozenset({0, 4}), time(7, 30))


@pytest.mark.parametrize("bad", [
    {"lock": [{"days": ["mon"], "at": "7h", "minutes": 20}]},
    {"lock": [{"days": ["noday"], "at": "07:00", "minutes": 20}]},
    {"lock": [{"days": ["mon"], "at": "07:00", "minutes": 0}]},
    {"override": {"phrase": "short"}},
    {"training": {"lead_in_seconds": 31}},
    {"recovery": {"recovery_minutes": 2}},
])
def test_parse__bad_values__rejected(bad):
    with pytest.raises(SettingsError):
        Settings.parse(bad)


def test_parse__default_config__valid_and_disabled():
    assert Settings.parse(tomllib.loads(DEFAULT_CONFIG)).enabled is False


def test_with_form__through_the_form_and_the_file__round_trips():
    # given
    current = Settings.parse({"lock": [{"days": ["mon", "fri"], "at": "07:30", "minutes": 20}],
                              "override": {"phrase": 'say "yes" please, really'}})
    form = current.to_form()
    assert form["locks"] == [{"days": ["mon", "fri"], "at": "07:30", "minutes": 20}]
    form["locks"].append({"days": ["sat", "sun"], "at": "10:00", "minutes": "45"})
    form["enabled"] = True
    # when
    updated = current.with_form(form)
    again = Settings.parse(tomllib.loads(dump(updated)))
    # then
    assert again == updated
    assert len(again.locks) == 2
    assert again.override_phrase == 'say "yes" please, really'


@pytest.mark.parametrize("change", [
    {"locks": [{"days": [], "at": "18:00", "minutes": 30}]},
    {"locks": [{"days": ["mon"], "at": "25:00", "minutes": 30}]},
    {"locks": [{"days": ["mon"], "at": "18:00", "minutes": "abc"}]},
    {"locks": [{"days": ["mon"], "at": "18:00", "minutes": 500}]},
    {"lead_in_seconds": 99},
])
def test_with_form__bad_values__rejected(change):
    current = Settings.parse({})
    with pytest.raises(SettingsError):
        current.with_form({**current.to_form(), **change})

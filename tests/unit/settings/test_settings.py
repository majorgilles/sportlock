import tomllib
from datetime import time

import pytest

from sportlock.settings.domain.settings import Settings, SettingsError
from sportlock.settings.infrastructure.config_toml import DEFAULT_CONFIG, dump


def test_parse__lock_days_and_time__read():
    settings = Settings.parse({"lock": [{"days": ["mon", "Friday"], "at": "07:30", "minutes": 20}]})
    assert (settings.locks[0].days, settings.locks[0].at) == (frozenset({0, 4}), time(7, 30))


@pytest.mark.parametrize(
    "bad",
    [
        {"lock": [{"days": ["mon"], "at": "7h", "minutes": 20}]},
        {"lock": [{"days": ["noday"], "at": "07:00", "minutes": 20}]},
        {"lock": [{"days": ["mon"], "at": "07:00", "minutes": 0}]},
        {"override": {"phrase": "short"}},
        {"training": {"lead_in_seconds": 31}},
        {"recovery": {"recovery_minutes": 2}},
    ],
)
def test_parse__bad_values__rejected(bad):
    with pytest.raises(SettingsError):
        Settings.parse(bad)


def test_parse__default_config__valid_and_disabled():
    assert Settings.parse(tomllib.loads(DEFAULT_CONFIG)).enabled is False


def test_with_form__through_the_form_and_the_file__round_trips():
    # given
    current = Settings.parse(
        {
            "lock": [{"days": ["mon", "fri"], "at": "07:30", "minutes": 20}],
            "override": {"phrase": 'say "yes" please, really'},
        }
    )
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


@pytest.mark.parametrize(
    "change",
    [
        {"locks": [{"days": [], "at": "18:00", "minutes": 30}]},
        {"locks": [{"days": ["mon"], "at": "25:00", "minutes": 30}]},
        {"locks": [{"days": ["mon"], "at": "18:00", "minutes": "abc"}]},
        {"locks": [{"days": ["mon"], "at": "18:00", "minutes": 500}]},
        {"lead_in_seconds": 99},
    ],
)
def test_with_form__bad_values__rejected(change):
    current = Settings.parse({})
    with pytest.raises(SettingsError):
        current.with_form({**current.to_form(), **change})


def test_parse__music_playlists__share_links_stored_as_uris():
    settings = Settings.parse(
        {
            "music": {
                "playlist": [
                    {"name": "Beast mode", "link": "https://open.spotify.com/playlist/37i9dQZF1DX76Wlfdnj7AP?si=x"}
                ]
            }
        }
    )
    assert [(p.name, p.uri) for p in settings.music_playlists] == [
        ("Beast mode", "spotify:playlist:37i9dQZF1DX76Wlfdnj7AP")
    ]


def test_parse__music_playlist_with_a_bad_link__rejected_with_its_number():
    with pytest.raises(SettingsError, match="music playlist #2"):
        Settings.parse(
            {
                "music": {
                    "playlist": [
                        {"name": "Ok", "link": "spotify:album:4aawyAB9vmqN3uQ7FjRGTy"},
                        {"name": "Bad", "link": "https://youtube.com/x"},
                    ]
                }
            }
        )


def test_with_form__music_playlists__round_trip_through_the_file():
    # given
    form = Settings.parse({}).to_form()
    form["music_playlists"] = [
        {"name": 'My "gym" mix', "link": "https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy"}
    ]
    # when
    updated = Settings.parse({}).with_form(form)
    again = Settings.parse(tomllib.loads(dump(updated)))
    # then
    assert again == updated
    assert again.to_form()["music_playlists"] == [
        {"name": 'My "gym" mix', "link": "spotify:album:4aawyAB9vmqN3uQ7FjRGTy"}
    ]


def test_with_form__without_music_playlists__keeps_the_current_ones():
    current = Settings.parse(
        {"music": {"playlist": [{"name": "Run", "link": "spotify:playlist:37i9dQZF1DX76Wlfdnj7AP"}]}}
    )
    form = current.to_form()
    del form["music_playlists"]
    assert current.with_form(form).music_playlists == current.music_playlists

import pytest

from sportlock.music.domain.music import MusicError, Playlist, client_id_of, spotify_uri
from sportlock.music.infrastructure.spotify_mpris import parse_now_playing
from sportlock.music.infrastructure.spotify_web import playlists_from_pages

PLAYLIST_ID = "37i9dQZF1DX76Wlfdnj7AP"


@pytest.mark.parametrize(
    "link",
    [
        f"spotify:playlist:{PLAYLIST_ID}",
        f"https://open.spotify.com/playlist/{PLAYLIST_ID}",
        f"https://open.spotify.com/playlist/{PLAYLIST_ID}?si=4f1c2a9b8e7d4c3a",
        f"https://open.spotify.com/intl-fr/playlist/{PLAYLIST_ID}",
        f"  spotify:playlist:{PLAYLIST_ID}\n",
    ],
)
def test_spotify_uri__uri_or_share_link__the_uri(link):
    assert spotify_uri(link) == f"spotify:playlist:{PLAYLIST_ID}"


def test_spotify_uri__track_link__a_track_uri():
    assert (
        spotify_uri("https://open.spotify.com/track/0du7mdWntlT18xxF2fqHg8") == "spotify:track:0du7mdWntlT18xxF2fqHg8"
    )


@pytest.mark.parametrize(
    "link",
    [
        "",
        "workout",
        "spotify:playlist:short",
        f"https://example.com/playlist/{PLAYLIST_ID}",
        f"spotify:user:{PLAYLIST_ID}",
    ],
)
def test_spotify_uri__not_a_spotify_link__refused(link):
    with pytest.raises(MusicError):
        spotify_uri(link)


def test_playlist_of__blank_name__refused():
    with pytest.raises(MusicError):
        Playlist.of("  ", f"spotify:playlist:{PLAYLIST_ID}")


def test_parse_now_playing__busctl_output__the_current_track():
    output = (
        '{"type":"s","data":"Playing"}\n'
        '{"type":"a{sv}","data":{"mpris:artUrl":{"type":"s","data":"https://i.scdn.co/image/abc"},'
        '"xesam:album":{"type":"s","data":"Full Moon"},"xesam:artist":{"type":"as","data":["Maromaro","Guest"]},'
        '"xesam:title":{"type":"s","data":"Moonlighting"}}}\n'
    )
    now = parse_now_playing(output)
    assert (now.running, now.playing, now.title, now.artist, now.album, now.art_url) == (
        True,
        True,
        "Moonlighting",
        "Maromaro, Guest",
        "Full Moon",
        "https://i.scdn.co/image/abc",
    )


def test_parse_now_playing__nothing_loaded__running_without_a_track():
    now = parse_now_playing('{"type":"s","data":"Stopped"}\n{"type":"a{sv}","data":{}}\n')
    assert (now.running, now.playing, now.title) == (True, False, "")


def test_playlists_from_pages__api_pages__liked_songs_first_then_playlists_with_a_small_cover():
    pages = [
        {
            "items": [
                {
                    "name": "Beast mode",
                    "uri": f"spotify:playlist:{PLAYLIST_ID}",
                    "images": [
                        {"url": "big", "width": 640},
                        {"url": "medium", "width": 300},
                        {"url": "small", "width": 60},
                    ],
                },
                None,
            ]
        },
        {
            "items": [
                {
                    "name": "Run",
                    "uri": "spotify:playlist:0du7mdWntlT18xxF2fqHg8",
                    "images": [{"url": "only", "width": None}],
                }
            ]
        },
    ]
    playlists = playlists_from_pages(pages, "gilles")
    assert [(p.name, p.uri, p.image) for p in playlists] == [
        ("Liked Songs", "spotify:user:gilles:collection", ""),
        ("Beast mode", f"spotify:playlist:{PLAYLIST_ID}", "medium"),
        ("Run", "spotify:playlist:0du7mdWntlT18xxF2fqHg8", "only"),
    ]


def test_client_id_of__pasted_with_spaces_and_capitals__normalised():
    assert client_id_of("  0123456789ABCDEF0123456789abcdef ") == "0123456789abcdef0123456789abcdef"


def test_client_id_of__not_32_hex_characters__refused():
    with pytest.raises(MusicError):
        client_id_of("0123456789abcdef")


def test_parse_now_playing__with_the_volume__read():
    now = parse_now_playing('{"type":"s","data":"Paused"}\n{"type":"a{sv}","data":{}}\n{"type":"d","data":0.345434}\n')
    assert now.volume == 0.345434

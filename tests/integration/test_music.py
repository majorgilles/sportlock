from sportlock.music.domain.music import Library, Playlist
from tests.world import CONFIG, World

PLAYLIST = "spotify:playlist:37i9dQZF1DX76Wlfdnj7AP"
WITH_PLAYLIST = CONFIG + f'\n[[music.playlist]]\nname = "Beast mode"\nlink = "{PLAYLIST}"\n'


def test_music_play__spotify_not_running__launched_with_the_playlist(world):
    assert world.cmd(cmd="music", action="play", link="https://open.spotify.com/playlist/37i9dQZF1DX76Wlfdnj7AP")["ok"]
    assert (world.music.launches, world.music.opened) == (1, [PLAYLIST])


def test_music_play__spotify_running__opened_without_relaunching(world):
    world.music.launch()
    world.cmd(cmd="music", action="play", link=PLAYLIST)
    assert (world.music.launches, world.music.opened) == (1, [PLAYLIST])


def test_music_toggle__spotify_not_running__launches_it(world):
    world.cmd(cmd="music", action="toggle")
    assert world.music.launches == 1


def test_music_toggle__spotify_running__plays_and_pauses(world):
    world.music.launch()
    world.cmd(cmd="music", action="toggle")
    assert world.music.state.playing is True


def test_music_next__spotify_not_running__refused(world):
    response = world.cmd(cmd="music", action="next")
    assert response == {"ok": False, "error": "Spotify isn't running"}


def test_music_play__bad_link__refused(world):
    assert not world.cmd(cmd="music", action="play", link="https://youtube.com/watch?v=x")["ok"]
    assert world.music.launches == 0


def test_music__unknown_action__refused(world):
    assert world.cmd(cmd="music", action="shuffle") == {"ok": False, "error": "unknown music action 'shuffle'"}


def test_state__during_a_lock__music_panel_with_the_playlists(tmp_path, catalogue):
    world = World(tmp_path, catalogue, config=WITH_PLAYLIST)
    assert world.at("17:00")["music"] is None
    world.cmd(cmd="start", minutes=20)
    music = world.at("17:01")["music"]
    assert music["playlists"] == [{"name": "Beast mode", "uri": PLAYLIST, "image": ""}]
    assert music["player"]["running"] is False


def test_state__spotify_account_connected__its_playlists_in_the_music_panel(world):
    # given
    liked = Playlist(name="Liked Songs", uri="spotify:user:gilles:collection")
    world.music_library.state = Library(connected=True, playlists=(liked,))
    world.cmd(cmd="start", minutes=20)
    # when
    library = world.at("17:01")["music"]["library"]
    # then
    assert library == {
        "connected": True,
        "loading": False,
        "error": "",
        "playlists": [{"name": "Liked Songs", "uri": "spotify:user:gilles:collection", "image": ""}],
    }


def test_music_play__liked_songs__opened(world):
    world.music.launch()
    world.cmd(cmd="music", action="play", link="spotify:user:gilles:collection")
    assert world.music.opened == ["spotify:user:gilles:collection"]

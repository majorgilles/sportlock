from sportlock.music.domain.music import Library, NowPlaying, Playlist
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
    assert world.cmd(cmd="music", action="shuffle") == {"ok": False, "error": "invalid music request: 'shuffle'"}


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


CLIENT_ID = "0123456789abcdef0123456789abcdef"


def _login(world, client_id: str) -> dict:
    assert world.cmd(cmd="music-login", client_id=client_id)["ok"]
    world.daemon.music_login_thread.join(5)
    return world.cmd(cmd="music-account")


def test_music_login__user_allows_access__connected_with_the_client_id_remembered(world):
    status = _login(world, CLIENT_ID.upper())
    assert (status["connecting"], status["error"]) == (False, "")
    assert (status["account"]["connected"], status["account"]["name"], status["account"]["client_id"]) == (
        True,
        "Gilles",
        CLIENT_ID,
    )


def test_music_login__refused_in_the_browser__the_error_for_the_settings_screen(world):
    world.music_account.refuse = "no answer from Spotify: access_denied"
    status = _login(world, CLIENT_ID)
    assert (status["account"]["connected"], status["error"]) == (False, "no answer from Spotify: access_denied")


def test_music_login__bad_client_id__refused_at_once(world):
    response = world.cmd(cmd="music-login", client_id="my-app")
    assert not response["ok"] and "Client ID" in response["error"]
    assert world.daemon.music_login_thread is None


def test_music_logout__connected__disconnected(world):
    _login(world, CLIENT_ID)
    world.cmd(cmd="music-logout")
    assert world.cmd(cmd="music-account")["account"]["connected"] is False


def test_music_volume__slider__sets_spotify_volume(world):
    world.music.launch()
    assert world.cmd(cmd="music", action="volume", volume=0.4)["ok"]
    assert world.music.state.volume == 0.4


def test_music_volume__out_of_range__refused(world):
    world.music.launch()
    assert not world.cmd(cmd="music", action="volume", volume=1.5)["ok"]


def test_lock__spotify_running__full_volume_during_the_lock_then_put_back(world):
    # given
    world.music.state = NowPlaying(running=True, volume=0.3)
    # when
    world.cmd(cmd="start", minutes=20)
    world.at("17:01")
    during = world.music.state.volume
    world.at("17:25")
    # then
    assert (during, world.music.state.volume) == (1.0, 0.3)


def test_lock__spotify_not_running__volume_left_alone(world):
    world.cmd(cmd="start", minutes=20)
    world.at("17:01")
    world.at("17:25")
    assert world.music.state.volume is None

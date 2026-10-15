"""Controlling the music from the lock screen, and what the music panel shows."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from sportlock.music.domain.music import FULL_VOLUME, MusicError, spotify_uri
from sportlock.music.domain.ports import MusicLibraryProtocol, MusicPlayerProtocol
from sportlock.settings.application.settings_services import SettingsState
from sportlock.shared_kernel.base import ValueObject


class ControlMusicCommand(ValueObject):
    """One button on the music panel; `link` is the Spotify link or URI to play, `volume` the slider (0 to 1)."""

    action: Literal["toggle", "next", "previous", "play", "volume"]
    link: str = ""
    volume: float = Field(default=FULL_VOLUME, ge=0, le=1)


class ControlMusicService:
    """Play, pause, skip, set the volume, or start a playlist; starts Spotify (at full volume) when it isn't running."""

    def __init__(self, music_player: MusicPlayerProtocol) -> None:
        self.music_player = music_player

    def execute(self, command: ControlMusicCommand) -> None:
        """Raises MusicError for a bad link, or a skip while Spotify isn't running."""
        running = self.music_player.now_playing().running
        if command.action == "play":
            uri = spotify_uri(command.link)
            if running:
                self.music_player.open(uri)
            else:
                self.music_player.launch(uri)
        elif command.action == "toggle":
            if running:
                self.music_player.toggle()
            else:
                self.music_player.launch()
        elif not running:
            raise MusicError("Spotify isn't running")
        elif command.action == "volume":
            self.music_player.set_volume(command.volume)
        elif command.action == "next":
            self.music_player.next()
        else:
            self.music_player.previous()


class MusicSnapshotService:
    """The music panel's part of the state file.

    The player, the favorites from the settings, and the playlists in the user's Spotify account.
    """

    def __init__(
        self, music_player: MusicPlayerProtocol, music_library: MusicLibraryProtocol, settings_state: SettingsState
    ) -> None:
        self.music_player = music_player
        self.music_library = music_library
        self.settings_state = settings_state

    def execute(self) -> dict:
        """The music panel's data.

        Shaped {"player": {...}, "playlists": [favorites], "library": {"connected", "loading", "error", "playlists"}}.
        """
        return {
            "player": self.music_player.now_playing().model_dump(),
            "playlists": [p.model_dump() for p in self.settings_state.settings.music_playlists],
            "library": self.music_library.library().model_dump(mode="json"),
        }

"""Controlling the music from the lock screen, and what the music panel shows."""

from __future__ import annotations

from typing import Literal

from sportlock.music.domain.music import MusicError, spotify_uri
from sportlock.music.domain.ports import MusicLibraryProtocol, MusicPlayerProtocol
from sportlock.settings.application.settings_services import SettingsState
from sportlock.shared_kernel.base import ValueObject


class ControlMusicCommand(ValueObject):
    """One button on the music panel; `link` is the Spotify link or URI to play."""

    action: Literal["toggle", "next", "previous", "play"]
    link: str = ""


class ControlMusicService:
    """Play, pause, skip, or start a playlist; starts Spotify when it isn't running."""

    def __init__(self, player: MusicPlayerProtocol) -> None:
        self.player = player

    def execute(self, command: ControlMusicCommand) -> None:
        """Raises MusicError for a bad link, or a skip while Spotify isn't running."""
        running = self.player.now_playing().running
        if command.action == "play":
            uri = spotify_uri(command.link)
            if running:
                self.player.open(uri)
            else:
                self.player.launch(uri)
        elif command.action == "toggle":
            if running:
                self.player.toggle()
            else:
                self.player.launch()
        elif not running:
            raise MusicError("Spotify isn't running")
        elif command.action == "next":
            self.player.next()
        else:
            self.player.previous()


class MusicSnapshotService:
    """The music panel's part of the state file: the player, the favourites from the settings, and
    the playlists in the user's Spotify account."""

    def __init__(self, player: MusicPlayerProtocol, library: MusicLibraryProtocol, settings: SettingsState) -> None:
        self.player = player
        self.library = library
        self.settings = settings

    def execute(self) -> dict:
        """{"player": {...}, "playlists": [favourites], "library": {"connected", "loading", "error", "playlists"}}"""
        return {
            "player": self.player.now_playing().model_dump(),
            "playlists": [p.model_dump() for p in self.settings.settings.music_playlists],
            "library": self.library.library().model_dump(mode="json"),
        }

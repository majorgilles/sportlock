"""The music ports: the player, and the user's playlists."""

from __future__ import annotations

from typing import Protocol

from sportlock.music.domain.music import Library, MusicAccount, NowPlaying


class MusicPlayerProtocol(Protocol):
    """A desktop music player (Spotify)."""

    def now_playing(self) -> NowPlaying:
        """Whether it runs, and what it plays."""
        ...

    def launch(self, uri: str | None = None) -> None:
        """Start the player, then play `uri` once it is up."""
        ...

    def open(self, uri: str) -> None:
        """Play a URI in the running player."""
        ...

    def toggle(self) -> None:
        """Play or pause."""
        ...

    def next(self) -> None:
        """Skip to the next track."""
        ...

    def previous(self) -> None:
        """Back to the previous track."""
        ...

    def set_volume(self, volume: float) -> None:
        """Set the player's own volume, from 0 to 1."""
        ...


class MusicLibraryProtocol(Protocol):
    """The user's playlists in their music account."""

    def library(self) -> Library:
        """The playlists as last fetched, without waiting; stale ones are refreshed in the background."""
        ...


class MusicAccountProtocol(Protocol):
    """Connecting the user's music account."""

    def account(self) -> MusicAccount:
        """Whether an account is connected, as whom, and the app settings it was connected with."""
        ...

    def login(self, client_id: str) -> str:
        """Ask the user to allow access in the browser (blocks until they do); returns the account's name."""
        ...

    def logout(self) -> None:
        """Forget the account."""
        ...

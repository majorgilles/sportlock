"""The music ports: the player, and the user's playlists."""

from __future__ import annotations

from typing import Protocol

from sportlock.music.domain.music import Library, NowPlaying


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


class MusicLibraryProtocol(Protocol):
    """The user's playlists in their music account."""

    def library(self) -> Library:
        """The playlists as last fetched, without waiting; stale ones are refreshed in the background."""
        ...

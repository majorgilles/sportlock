"""Music on the lock screen: the playlists the user can start, and what the player is playing.

Spotify is driven through its desktop app, so a playlist is identified by its Spotify URI
(`spotify:playlist:…`); share links from the app are accepted and converted.
"""

from __future__ import annotations

import re

from sportlock.shared_kernel.base import ValueObject
from sportlock.shared_kernel.errors import DomainError

KINDS = ("track", "album", "playlist", "artist", "episode", "show")
_URI = re.compile(rf"^spotify:({'|'.join(KINDS)}):([A-Za-z0-9]{{22}})$")
_LIKED = re.compile(r"^spotify:user:[^:\s]+:collection$")
_LINK = re.compile(
    rf"^https?://open\.spotify\.com/(?:intl-[a-z-]+/)?({'|'.join(KINDS)})/([A-Za-z0-9]{{22}})(?:[/?#].*)?$"
)
MAX_PLAYLISTS = 20


class MusicError(DomainError):
    """A music request can't be carried out."""


def spotify_uri(link: str) -> str:
    """The `spotify:<kind>:<id>` URI of a Spotify URI or an open.spotify.com link (share links included)."""
    text = link.strip()
    if _LIKED.match(text):
        return text  # the user's Liked Songs
    match = _URI.match(text) or _LINK.match(text)
    if not match:
        raise MusicError(f"not a Spotify link: {text!r} (use Share → Copy link in Spotify)")
    return f"spotify:{match.group(1)}:{match.group(2)}"


class Playlist(ValueObject):
    """Something to start from the lock screen: a playlist, album, artist or song."""

    name: str
    uri: str
    image: str = ""

    @classmethod
    def of(cls, name: str, link: str) -> Playlist:
        """Validate a name and a Spotify link or URI."""
        if not name.strip():
            raise MusicError("a playlist needs a name")
        return cls(name=name.strip(), uri=spotify_uri(link))


class NowPlaying(ValueObject):
    """The player's state: not running, starting, or running with its current track."""

    running: bool = False
    starting: bool = False
    playing: bool = False
    title: str = ""
    artist: str = ""
    album: str = ""
    art_url: str = ""


class Library(ValueObject):
    """The user's own Spotify playlists, as last fetched from their account."""

    connected: bool = False
    loading: bool = False
    error: str = ""
    playlists: tuple[Playlist, ...] = ()

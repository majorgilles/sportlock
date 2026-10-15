"""The Spotify desktop app, driven over MPRIS (D-Bus, through `busctl`): no account key needed.

Spotify's `OpenUri` plays a track, album, artist or playlist URI, and its `Volume` property is
writable. Right after launching, the app takes a few seconds to appear on the bus, so a launch
turns the volume up and plays the URI in the background once Spotify answers.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from typing import override

from sportlock.music.domain.music import FULL_VOLUME, NowPlaying
from sportlock.music.domain.ports import MusicPlayerProtocol

VOLUME_INDEX = 2

BUS_NAME = "org.mpris.MediaPlayer2.spotify"
MPRIS_PATH = "/org/mpris/MediaPlayer2"
MPRIS_PLAYER = "org.mpris.MediaPlayer2.Player"
STARTUP_SECONDS = 30


def _busctl(*args: str, timeout: float = 2) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["busctl", "--user", *args], capture_output=True, text=True, timeout=timeout, check=False)
    except OSError, subprocess.TimeoutExpired:
        return None


def parse_now_playing(output: str) -> NowPlaying:
    """`busctl --json=short get-property … PlaybackStatus Metadata Volume` output, one JSON value per line."""
    try:
        values = [json.loads(line)["data"] for line in output.strip().splitlines()]
        status, metadata = values[0], values[1]
    except ValueError, KeyError, TypeError, IndexError:
        return NowPlaying(running=True)
    volume = (
        values[VOLUME_INDEX] if len(values) > VOLUME_INDEX and isinstance(values[VOLUME_INDEX], (int, float)) else None
    )

    def text(key: str) -> str:
        value = metadata.get(key, {}).get("data", "")
        return ", ".join(value) if isinstance(value, list) else str(value)

    return NowPlaying(
        running=True,
        playing=status == "Playing",
        title=text("xesam:title"),
        artist=text("xesam:artist"),
        album=text("xesam:album"),
        art_url=text("mpris:artUrl"),
        volume=min(1.0, max(0.0, float(volume))) if volume is not None else None,
    )


class SpotifyMprisPlayer(MusicPlayerProtocol):
    """MusicPlayerProtocol over the Spotify desktop app."""

    def __init__(self) -> None:
        self.launched_at: float | None = None

    def _call(self, method: str, *args: str) -> bool:
        result = _busctl("call", BUS_NAME, MPRIS_PATH, MPRIS_PLAYER, method, *args)
        return bool(result and result.returncode == 0)

    def _starting(self) -> bool:
        return self.launched_at is not None and time.monotonic() - self.launched_at < STARTUP_SECONDS

    @override
    def now_playing(self) -> NowPlaying:
        result = _busctl(
            "--json=short", "get-property", BUS_NAME, MPRIS_PATH, MPRIS_PLAYER, "PlaybackStatus", "Metadata", "Volume"
        )
        if not result or result.returncode != 0:
            return NowPlaying(starting=self._starting())
        self.launched_at = None
        return parse_now_playing(result.stdout)

    @override
    def launch(self, uri: str | None = None) -> None:
        """Start Spotify detached from the service (once); play `uri` once it is on the bus."""
        if not self._starting():
            command = ["uwsm-app", "--", "spotify"] if shutil.which("uwsm-app") else ["spotify"]
            try:
                subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError:
                return
            self.launched_at = time.monotonic()
        threading.Thread(target=self._when_up, args=(uri,), daemon=True).start()

    def _when_up(self, uri: str | None) -> None:
        # Spotify starts at the volume it was left at: turn it up for training, then play.
        deadline = time.monotonic() + STARTUP_SECONDS
        while time.monotonic() < deadline:
            if self._set_volume(FULL_VOLUME):
                if uri:
                    self._call("OpenUri", "s", uri)
                return
            time.sleep(0.5)

    def _set_volume(self, volume: float) -> bool:
        result = _busctl("set-property", BUS_NAME, MPRIS_PATH, MPRIS_PLAYER, "Volume", "d", f"{volume:.3f}")
        return bool(result and result.returncode == 0)

    @override
    def open(self, uri: str) -> None:
        """Play a URI in the running app."""
        self._call("OpenUri", "s", uri)

    @override
    def toggle(self) -> None:
        """Play or pause."""
        self._call("PlayPause")

    @override
    def next(self) -> None:
        """Next track."""
        self._call("Next")

    @override
    def previous(self) -> None:
        """Previous track."""
        self._call("Previous")

    @override
    def set_volume(self, volume: float) -> None:
        """Spotify's own volume."""
        self._set_volume(volume)

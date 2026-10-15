# Spotify through the desktop app (playback) and the Web API (playlists)

## Status

`accepted`

**Decision Date:** 2026-10-12

## Context

The lock is a Wayland session lock: no window can be seen or clicked while it is up, so the user
can't use Spotify during a session. They want to start, pause and skip music, and choose any of
their playlists, from the lock screen.

- The Spotify desktop app implements MPRIS on D-Bus: play/pause, next, previous, the current
  track with its cover, and `OpenUri`, which plays a track, album, artist, playlist or Liked Songs
  URI (also right after the app starts, once it is on the bus). MPRIS can't list playlists.
- Listing the user's playlists needs the Spotify Web API, which needs an app registered by the
  user (a Client ID). The authorization-code flow with PKCE needs no client secret.
- Spotify's local cache is encrypted browser storage: not an option.

## Decision

- **Playback goes through the desktop app over MPRIS** (`busctl`, no new dependency):
  `SpotifyMprisPlayer`. Starting a playlist while Spotify is closed launches it (`uwsm-app`) and
  retries `OpenUri` in the background until the app answers.
- **The playlists come from the Web API, read-only**, after a one-time login from the settings
  screen or `sportlock music login`; the service runs the login in a background thread
  (PKCE, loopback redirect `http://127.0.0.1:8974/callback`, scopes for reading playlists and Liked
  Songs): `SpotifyWebLibrary`. Tokens live in the key-value table; the access token is refreshed
  when it expires. The list is cached in memory and refreshed in a background thread every
  10 minutes while the music panel is shown, so a tick never waits on the network.
- **Favorites** (`[[music.playlist]]` in config.toml) work without the Web API and come first.
- The state file gets a `music` key during a lock (player, favorites, library); the lock screen
  sends `{"cmd": "music", "action": ..., "link": ...}` through `sportlock raw`.
- Locks still pause every playing player when they begin; the user picks the music.

## Consequences

### Positive

- Music works on the lock screen with no account key; the Web API is only needed for the full list.
- The service talks to Spotify only during a lock.

### Negative

- Listing every playlist needs a one-time developer app on the user's Spotify account.
- Only the Spotify desktop app is supported for playback.

## Files

- `sportlock/music/domain/music.py`: `Playlist`, `NowPlaying`, `Library`, `spotify_uri`
- `sportlock/music/domain/ports.py`: `MusicPlayerProtocol`, `MusicLibraryProtocol`
- `sportlock/music/application/music_services.py`: `ControlMusicService`, `MusicSnapshotService`
- `sportlock/music/application/music_account_services.py`: connect, disconnect, get the account
- `sportlock/app/window/shell.qml`: the Spotify account section
- `sportlock/music/infrastructure/spotify_mpris.py`, `spotify_web.py`
- `sportlock/locks/ui/lock_screen/shell.qml`: the music panel

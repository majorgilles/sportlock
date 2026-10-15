"""Connecting the user's Spotify account (which lists their playlists on the lock screen)."""

from __future__ import annotations

from sportlock.music.domain.music import MusicAccount, client_id_of
from sportlock.music.domain.ports import MusicAccountProtocol
from sportlock.shared_kernel.base import ValueObject


class ConnectMusicAccountCommand(ValueObject):
    """The Client ID of the user's Spotify app."""

    client_id: str


class ConnectMusicAccountService:
    """Logs in through the browser; blocks until the user allows access (run it in the background)."""

    def __init__(self, music_account: MusicAccountProtocol) -> None:
        self.music_account = music_account

    def execute(self, command: ConnectMusicAccountCommand) -> str:
        """Returns the account's name; raises MusicError for a bad Client ID or a refused login."""
        return self.music_account.login(client_id_of(command.client_id))


class DisconnectMusicAccountService:
    """Forgets the account."""

    def __init__(self, music_account: MusicAccountProtocol) -> None:
        self.music_account = music_account

    def execute(self) -> None:
        """The lock screen only shows the favorites afterwards."""
        self.music_account.logout()


class GetMusicAccountService:
    """Whether an account is connected, for the settings screen."""

    def __init__(self, music_account: MusicAccountProtocol) -> None:
        self.music_account = music_account

    def execute(self) -> MusicAccount:
        """The connection as stored."""
        return self.music_account.account()

"""The user's Spotify playlists, from the Spotify Web API (read-only).

Login is the OAuth authorization-code flow with PKCE, so only a Client ID is needed (no secret):
the user creates a Spotify app once, with the redirect URI below, and runs `sportlock music login`.
The tokens are kept in the database's key-value table; the access token is refreshed when it expires.
Playback still goes through the desktop app (`spotify_mpris.py`); the Web API only lists playlists.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import logging
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import override

from sportlock.music.domain.music import Library, MusicAccount, MusicError, Playlist
from sportlock.music.domain.ports import MusicAccountProtocol, MusicLibraryProtocol
from sportlock.shared_kernel.infrastructure.database import Database

log = logging.getLogger("sportlock")

TOKENS_KEY = "spotify"
REDIRECT_PORT = 8974
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}/callback"
SCOPES = "playlist-read-private playlist-read-collaborative user-library-read"
AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_URL = "https://api.spotify.com/v1"
REFRESH_SECONDS = 10 * 60
RETRY_SECONDS = 60
MAX_PLAYLISTS = 500


def _post_form(url: str, fields: dict) -> dict:
    data = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def open_in_browser(url: str) -> None:
    """Open a URL in the user's browser (also from the service)."""
    try:
        subprocess.Popen(
            ["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
        )
    except OSError:
        pass


def _cover(images: list[dict]) -> str:
    """The smallest cover still at least 120 px wide (Spotify lists them largest first; sizes may be null)."""
    sized = sorted((i for i in images if i.get("width")), key=lambda i: i["width"])
    fitting = [i for i in sized if i["width"] >= 120]
    chosen = fitting[0] if fitting else (images[0] if images else None)
    return chosen["url"] if chosen else ""


def playlists_from_pages(pages: list[dict], user_id: str) -> tuple[Playlist, ...]:
    """Liked Songs first, then the playlists of `GET /me/playlists` pages, in the account's order."""
    playlists = [Playlist(name="Liked Songs", uri=f"spotify:user:{user_id}:collection")] if user_id else []
    for page in pages:
        for item in page.get("items") or []:
            if not item or not item.get("uri") or not item.get("name"):
                continue
            playlists.append(Playlist(name=item["name"], uri=item["uri"], image=_cover(item.get("images") or [])))
    return tuple(playlists)


class SpotifyWebLibrary(MusicLibraryProtocol, MusicAccountProtocol):
    """The user's Spotify account and its playlists over the Web API, cached in memory and refreshed
    in the background."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self.cache: Library | None = None
        self.fetched_at = 0.0
        self.refreshing = False
        self.lock = threading.Lock()

    # -- the port ----------------------------------------------------------------------------

    @override
    def library(self) -> Library:
        tokens = self.database.get(TOKENS_KEY)
        if not tokens or not tokens.get("refresh_token"):
            self.cache = None
            return Library()
        with self.lock:
            age = time.monotonic() - self.fetched_at
            stale = self.cache is None or age > (RETRY_SECONDS if self.cache.error else REFRESH_SECONDS)
            if stale and not self.refreshing:
                self.refreshing = True
                threading.Thread(target=self._refresh, daemon=True).start()
            if self.cache is None:
                return Library(connected=True, loading=True)
            return self.cache

    # -- the account -------------------------------------------------------------------------

    @override
    def account(self) -> MusicAccount:
        tokens = self.database.get(TOKENS_KEY) or {}
        return MusicAccount(
            connected=bool(tokens.get("refresh_token")),
            name=tokens.get("name", ""),
            client_id=tokens.get("client_id", ""),
            redirect_uri=REDIRECT_URI,
        )

    @override
    def login(self, client_id: str, *, open_browser=open_in_browser, timeout: float = 300) -> str:
        """Ask the user to allow sportlock in the browser; store the tokens. Returns the account's name."""
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)
        query = urllib.parse.urlencode(
            {
                "client_id": client_id,
                "response_type": "code",
                "redirect_uri": REDIRECT_URI,
                "code_challenge_method": "S256",
                "code_challenge": challenge,
                "scope": SCOPES,
                "state": state,
            }
        )
        code = self._wait_for_code(f"{AUTHORIZE_URL}?{query}", state, open_browser, timeout)
        try:
            answer = _post_form(
                TOKEN_URL,
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": REDIRECT_URI,
                    "client_id": client_id,
                    "code_verifier": verifier,
                },
            )
        except urllib.error.HTTPError as error:
            raise MusicError(f"Spotify refused the login: {error.read().decode(errors='replace')}") from None
        self._store(client_id, answer)
        me = self._get("/me")
        name = str(me.get("display_name") or me.get("id") or "")
        self.database.put(TOKENS_KEY, {**self.database.get(TOKENS_KEY), "user_id": me.get("id", ""), "name": name})
        with self.lock:
            self.cache, self.fetched_at = None, 0.0
        return name

    @override
    def logout(self) -> None:
        """Forget the tokens; keep the Client ID for the next login."""
        client_id = self.account().client_id
        if client_id:
            self.database.put(TOKENS_KEY, {"client_id": client_id})
        else:
            self.database.delete(TOKENS_KEY)
        self.cache = None

    def _wait_for_code(self, url: str, state: str, open_browser, timeout: float) -> str:
        result: dict[str, str] = {}

        class Callback(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 (http.server's naming)
                """Spotify redirects the browser here with the code."""
                params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
                if params.get("state") == state:
                    result.update(params)
                ok = "code" in params and params.get("state") == state
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                message = "sportlock is connected to Spotify. You can close this tab." if ok else "Login failed."
                self.wfile.write(f"<p style='font:18px sans-serif;margin:3em'>{message}</p>".encode())

            def log_message(self, *args) -> None:
                """Quiet."""

        try:
            server = http.server.HTTPServer(("127.0.0.1", REDIRECT_PORT), Callback)
        except OSError:
            raise MusicError(f"port {REDIRECT_PORT} is busy; close what uses it and try again") from None
        server.timeout = 1
        open_browser(url)
        deadline = time.monotonic() + timeout
        with server:
            while not result and time.monotonic() < deadline:
                server.handle_request()
        if "code" not in result:
            raise MusicError(f"no answer from Spotify: {result.get('error', 'timed out')}")
        return result["code"]

    # -- tokens and requests -----------------------------------------------------------------

    def _store(self, client_id: str, answer: dict) -> None:
        previous = self.database.get(TOKENS_KEY) or {}
        self.database.put(
            TOKENS_KEY,
            {
                **previous,
                "client_id": client_id,
                "access_token": answer["access_token"],
                "refresh_token": answer.get("refresh_token") or previous.get("refresh_token"),
                "expires_at": time.time() + int(answer.get("expires_in", 3600)) - 60,
            },
        )

    def _access_token(self) -> str:
        tokens = self.database.get(TOKENS_KEY)
        if not tokens or not tokens.get("refresh_token"):
            raise MusicError("not logged in to Spotify (sportlock music login)")
        if time.time() < tokens["expires_at"]:
            return tokens["access_token"]
        try:
            answer = _post_form(
                TOKEN_URL,
                {
                    "grant_type": "refresh_token",
                    "refresh_token": tokens["refresh_token"],
                    "client_id": tokens["client_id"],
                },
            )
        except urllib.error.HTTPError as error:
            if error.code in (400, 401):
                raise MusicError("Spotify login expired; run `sportlock music login` again") from None
            raise
        self._store(tokens["client_id"], answer)
        return answer["access_token"]

    def _get(self, path: str) -> dict:
        url = path if path.startswith("https://") else API_URL + path
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._access_token()}"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

    def _refresh(self) -> None:
        try:
            pages, url = [], "/me/playlists?limit=50"
            while url and len(pages) * 50 < MAX_PLAYLISTS:
                page = self._get(url)
                pages.append(page)
                url = page.get("next")
            user_id = (self.database.get(TOKENS_KEY) or {}).get("user_id", "")
            library = Library(connected=True, playlists=playlists_from_pages(pages, user_id))
        except (MusicError, OSError, ValueError, KeyError) as error:
            log.warning("spotify playlists: %s", error)
            message = str(error) if isinstance(error, MusicError) else "Spotify can't be reached"
            library = (self.cache or Library(connected=True)).model_copy(update={"error": message, "loading": False})
        with self.lock:
            self.cache = library
            self.fetched_at = time.monotonic()
            self.refreshing = False

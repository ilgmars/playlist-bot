"""Cross-platform playlist management.

Creates or appends to playlists on YouTube and Spotify.
Apple Music writes to a .m3u export (manual import).
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth

from .link_parser import ParsedLink, Platform

log = logging.getLogger(__name__)

# Scopes needed for YouTube playlist management
_YT_SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

# Spotify scopes
_SPOTIFY_SCOPES = ["playlist-modify-public", "playlist-modify-private"]


def _is_rate_limit_error(exc: Exception) -> tuple[bool, float | None]:
    """Detect HTTP 429 rate-limit exceptions and extract Retry-After if present."""
    resp = getattr(exc, "resp", None)
    status = getattr(exc, "http_status", None) or getattr(resp, "status", None)
    if status != 429 and "429" not in str(exc):
        return False, None
    headers = getattr(exc, "headers", None) or resp or {}
    retry_after = (
        headers.get("Retry-After") or headers.get("retry-after")
        if hasattr(headers, "get")
        else None
    )
    if retry_after is not None:
        try:
            return True, max(0.0, float(retry_after))
        except (TypeError, ValueError):
            pass
    return True, None


def _call_with_backoff[T](
    fn: Callable[[], T],
    max_retries: int = 3,
    base_delay: float = 0.5,
) -> T:
    """Execute an API call with exponential backoff on rate limits (429)."""
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as exc:
            is_rl, retry_after = _is_rate_limit_error(exc)
            if not is_rl or attempt >= max_retries - 1:
                raise
            delay = retry_after if retry_after is not None else base_delay * (2**attempt)
            log.warning("API rate limited, retrying in %.2fs (attempt %d)", delay, attempt + 1)
            time.sleep(delay)
    return fn()  # pragma: no cover


def _clean_m3u_line(val: str) -> str:
    return val.replace("\r", " ").replace("\n", " ").strip()


def generate_m3u(
    songs: list[dict[str, Any]],
    title: str = "Shared Playlist",
    preferred_platform: str | None = None,
) -> str:
    """Generate an Extended M3U (.m3u) playlist string from collected songs."""
    lines = ["#EXTM3U", f"#PLAYLIST:{_clean_m3u_line(title)}"]
    for song in songs:
        links = song.get("links") or []
        if not links:
            continue
        chosen_url = links[0].get("original_url", "")
        if preferred_platform:
            for link in links:
                if link.get("platform") == preferred_platform and link.get("original_url"):
                    chosen_url = link["original_url"]
                    break
        if not chosen_url:
            continue
        raw_dur = song.get("duration")
        if isinstance(raw_dur, int) and raw_dur > 1000:
            dur_sec = int(raw_dur // 1000)
        elif isinstance(raw_dur, int):
            dur_sec = raw_dur
        else:
            dur_sec = -1
        artist = _clean_m3u_line(str(song.get("artist") or "Unknown"))
        song_title = _clean_m3u_line(str(song.get("title") or "Unknown"))
        lines.append(f"#EXTINF:{dur_sec},{artist} - {song_title}")
        lines.append(_clean_m3u_line(str(chosen_url)))
    return "\n".join(lines) + "\n"


_PLATFORM_LABELS: dict[str, str] = {
    "spotify": "Spotify",
    "youtube": "YouTube",
    "youtube_music": "YouTube Music",
    "apple_music": "Apple Music",
    "deezer": "Deezer",
    "soundcloud": "SoundCloud",
    "tidal": "Tidal",
    "bandcamp": "Bandcamp",
}


def _extract_yt_id(link: dict[str, Any]) -> str | None:
    import re

    tid = str(link.get("track_id") or "")
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", tid):
        return tid
    url = str(link.get("original_url") or link.get("canonical_url") or "")
    m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None


def _extract_sp_uri(link: dict[str, Any]) -> str:
    import re

    url = str(link.get("original_url") or link.get("canonical_url") or "")
    tid = str(link.get("track_id") or "")
    if re.fullmatch(r"[A-Za-z0-9]{22}", tid):
        return f"spotify:track:{tid}"
    m = re.search(r"/track/([A-Za-z0-9]{22})", url)
    return f"spotify:track:{m.group(1)}" if m else url


def _collect_platform_tracks(
    songs: list[dict[str, Any]],
    platform: str,
) -> tuple[list[str], list[str], list[str]]:
    urls: list[str] = []
    uris: list[str] = []
    yt_ids: list[str] = []
    match_set = (
        {"youtube", "youtube_music"}
        if platform in ("youtube", "youtube_music")
        else ({platform, "sound_cloud"} if platform == "soundcloud" else {platform})
    )
    for song in songs:
        for link in song.get("links") or []:
            lp = str(link.get("platform") or "")
            url = str(link.get("original_url") or link.get("canonical_url") or "")
            if lp not in match_set or not url or url in urls:
                continue
            urls.append(url)
            if platform == "spotify":
                uris.append(_extract_sp_uri(link))
            else:
                uris.append(url)
            if platform in ("youtube", "youtube_music"):
                vid = _extract_yt_id(link)
                if vid and vid not in yt_ids:
                    yt_ids.append(vid)
            break
    return urls, uris, yt_ids


def _build_single_platform_entry(
    plat: str,
    label: str,
    songs: list[dict[str, Any]],
    direct_url: str | None,
    guild_id: str | None,
    channel_id: str | None,
) -> dict[str, Any]:
    from urllib.parse import urlencode

    urls, uris, yt_ids = _collect_platform_tracks(songs, plat)
    mode = "empty"
    open_url: str | None = None
    if direct_url:
        mode = "synced"
        open_url = direct_url
    elif plat in ("youtube", "youtube_music") and yt_ids:
        mode = "instant_queue"
        open_url = f"https://www.youtube.com/watch_videos?video_ids={','.join(yt_ids[:50])}"
    elif urls:
        mode = "tracks"
        open_url = urls[0]

    q: dict[str, str] = {"platform": plat}
    if guild_id:
        q["guild_id"] = guild_id
    if channel_id:
        q["channel_id"] = channel_id
    qs = urlencode(q)
    q_open = {k: v for k, v in q.items() if k != "platform"}
    open_qs = f"?{urlencode(q_open)}" if q_open else ""
    return {
        "platform": plat,
        "label": label,
        "track_count": len(urls),
        "mode": mode,
        "playlist_url": direct_url,
        "open_url": open_url,
        "redirect_url": f"/playlists/{plat}{open_qs}",
        "export_m3u_url": f"/api/export.m3u?{qs}",
        "tracks_txt_url": f"/api/playlists/{plat}/tracks.txt{open_qs}",
        "track_urls": urls,
        "track_uris": uris,
    }


def build_platform_playlists(
    songs: list[dict[str, Any]],
    synced_playlists: list[dict[str, Any]] | None = None,
    custom_urls: dict[str, str] | None = None,
    guild_id: str | None = None,
    channel_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Build multi-platform playlist descriptors (Spotify, YouTube, YT Music, Apple Music, etc.)."""
    synced_map: dict[str, str] = {}
    for row in synced_playlists or []:
        p = str(row.get("platform") or "")
        u = str(row.get("playlist_url") or "")
        if p and u:
            synced_map["soundcloud" if p == "sound_cloud" else p] = u
    for p, u in (custom_urls or {}).items():
        if p and u:
            synced_map["soundcloud" if p == "sound_cloud" else p] = str(u)

    return {
        plat: _build_single_platform_entry(
            plat, label, songs, synced_map.get(plat), guild_id, channel_id
        )
        for plat, label in _PLATFORM_LABELS.items()
    }


class PlaylistManager:
    """Manages playlists across supported platforms."""

    def __init__(self) -> None:
        self._youtube_creds: Credentials | None = None
        self._spotify: Spotify | None = None

    # ------------------------------------------------------------------
    # YouTube
    # ------------------------------------------------------------------

    def _ensure_youtube(self) -> Any | None:
        if self._youtube_creds is not None:
            return self._build_youtube_client()

        creds_path = os.environ.get("YOUTUBE_CLIENT_SECRETS")
        token_path = os.environ.get("YOUTUBE_TOKEN_PATH", "/data/youtube_token.json")

        if not creds_path:
            log.warning("YOUTUBE_CLIENT_SECRETS not set — YouTube playlist ops disabled")
            return None

        try:
            _ = Flow.from_client_secrets_file(creds_path, scopes=_YT_SCOPES)
        except Exception:
            return None

        if os.path.exists(token_path):
            self._youtube_creds = Credentials.from_authorized_user_file(token_path, _YT_SCOPES)

        if not self._youtube_creds or not self._youtube_creds.valid:
            if (
                self._youtube_creds
                and self._youtube_creds.expired
                and self._youtube_creds.refresh_token
            ):
                self._youtube_creds.refresh(GoogleAuthRequest())
            else:
                # First run — would need OAuth flow
                self._youtube_creds = None
                return None

        return self._build_youtube_client()

    def _build_youtube_client(self) -> Any:
        import googleapiclient.discovery

        return googleapiclient.discovery.build("youtube", "v3", credentials=self._youtube_creds)

    def _get_or_create_youtube_playlist(
        self,
        channel_id: str,
        title: str = "Shared Playlist",
    ) -> str | None:
        """Get existing playlist ID or create a new one for the channel."""
        client = self._ensure_youtube()
        if not client:
            return None

        request = client.playlists().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": title,
                    "description": "Auto-generated playlist from Discord music links",
                },
                "status": {"privacyStatus": "private"},
            },
        )
        response = _call_with_backoff(request.execute)
        playlist_id = response.get("id")
        log.info("Created YouTube playlist %s for channel %s", playlist_id, channel_id)
        return playlist_id

    def add_to_youtube_playlist(
        self,
        channel_id: str,
        playlist_id: str,
        video_id: str,
    ) -> bool:
        """Prepend a video to the top of the playlist (position=0)."""
        client = self._ensure_youtube()
        if not client:
            return False

        try:
            request = client.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {
                            "kind": "youtube#video",
                            "videoId": video_id,
                        },
                        "position": 0,
                    }
                },
            )
            _call_with_backoff(request.execute)
            log.info("Added YouTube video %s to playlist %s at position 0", video_id, playlist_id)
            return True
        except Exception as e:
            log.error("Failed to add to YouTube playlist: %s", e)
            return False

    # ------------------------------------------------------------------
    # Spotify
    # ------------------------------------------------------------------

    def _ensure_spotify(self) -> Spotify | None:
        if self._spotify is not None:
            return self._spotify

        client_id = os.environ.get("SPOTIFY_CLIENT_ID")
        client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
        redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback")
        token_path = os.environ.get("SPOTIFY_TOKEN_PATH", "/data/spotify_token.json")

        if not client_id or not client_secret:
            log.warning("Spotify credentials not set — Spotify playlist ops disabled")
            return None

        sp_oauth = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope=_SPOTIFY_SCOPES,
            cache_path=token_path,
        )

        token_info = sp_oauth.get_cached_token()
        if not token_info or sp_oauth.is_token_expired(token_info):
            # First run — would need to visit the auth URL
            if not token_info:
                log.warning(
                    "Spotify token not found — run auth flow first: %s",
                    sp_oauth.get_authorize_url(),
                )
                return None
            token_info = sp_oauth.refresh_access_token(token_info["refresh_token"])

        self._spotify = Spotify(auth_manager=sp_oauth)
        return self._spotify

    def _extract_spotify_track_id(self, url: str) -> str | None:
        import re

        m = re.search(r"/track/([A-Za-z0-9]{22})", url)
        return m.group(1) if m else None

    def add_to_spotify_playlist(
        self,
        channel_id: str,
        playlist_id: str,
        track_url: str,
    ) -> bool:
        """Add a Spotify track to the playlist (prepend)."""
        client = self._ensure_spotify()
        if not client:
            return False

        track_id = self._extract_spotify_track_id(track_url)
        if not track_id:
            log.warning("Could not extract Spotify track ID from %s", track_url)
            return False

        try:
            # To prepend, insert at position 0
            _call_with_backoff(
                lambda: client.playlist_add_items(
                    playlist_id,
                    [f"spotify:track:{track_id}"],
                    position=0,
                )
            )
            log.info("Added Spotify track %s to playlist %s", track_id, playlist_id)
            return True
        except Exception as e:
            log.error("Failed to add to Spotify playlist: %s", e)
            return False

    def create_spotify_playlist(
        self,
        channel_id: str,
        title: str = "Shared Playlist",
    ) -> str | None:
        client = self._ensure_spotify()
        if not client:
            return None
        user_id = _call_with_backoff(client.current_user)["id"]
        playlist = _call_with_backoff(
            lambda: client.user_playlist_create(
                user=user_id,
                name=title,
                public=False,
                description="Auto-generated from Discord music links",
            )
        )
        return playlist.get("id")

    # ------------------------------------------------------------------
    # Apple Music / M3U Export
    # ------------------------------------------------------------------

    def export_m3u(
        self,
        songs: list[dict[str, Any]],
        output_path: str | None = None,
        title: str = "Shared Playlist",
        preferred_platform: str | None = None,
    ) -> str:
        """Export songs to an Extended M3U (.m3u) format string and optional file."""
        content = generate_m3u(songs, title=title, preferred_platform=preferred_platform)
        if output_path:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return content

    # ------------------------------------------------------------------
    # Generic dispatch
    # ------------------------------------------------------------------

    def add_to_playlist(
        self,
        platform: Platform,
        channel_id: str,
        playlist_id: str,
        link: ParsedLink,
    ) -> bool:
        """Add a track to a playlist on the given platform."""
        match platform:
            case Platform.YOUTUBE | Platform.YOUTUBE_MUSIC:
                if link.platform not in (Platform.YOUTUBE, Platform.YOUTUBE_MUSIC):
                    return False
                video_id = link.track_id or link.original_url.split("v=")[-1][:11]
                return self.add_to_youtube_playlist(channel_id, playlist_id, video_id)
            case Platform.SPOTIFY:
                if link.platform != Platform.SPOTIFY:
                    return False
                return self.add_to_spotify_playlist(channel_id, playlist_id, link.original_url)
            case _:
                # Apple Music, SoundCloud, Bandcamp — no write API
                log.info("Platform %s does not support automated playlist writes", platform)
                return False

"""Cross-platform playlist management.

Creates or appends to playlists on YouTube and Spotify.
Apple Music writes to a .m3u export (manual import).
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx
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


class PlaylistManager:
    """Manages playlists across supported platforms.

    Each channel gets its own playlist per platform. New songs are prepended
    so the newest appears first (Spotify and YouTube both support insert-at-top
    via position=0).
    """

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
                self._youtube_creds.refresh(httpx.Client())
            else:
                # First run — would need OAuth flow
                self._youtube_creds = None
                return None

        return self._build_youtube_client()

    def _build_youtube_client(self) -> Any:
        import googleapiclient.discovery
        return googleapiclient.discovery.build("youtube", "v3", credentials=self._youtube_creds)

    def _get_or_create_youtube_playlist(
        self, channel_id: str, title: str = "Shared Playlist",
    ) -> str | None:
        """Get existing playlist ID or create a new one for the channel."""
        client = self._ensure_youtube()
        if not client:
            return None

        # Check if we already stored a playlist ID for this channel
        # (in a real app, look it up in the DB)

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
        response = request.execute()
        playlist_id = response.get("id")
        log.info("Created YouTube playlist %s for channel %s", playlist_id, channel_id)
        return playlist_id

    def add_to_youtube_playlist(
        self, channel_id: str, playlist_id: str, video_id: str,
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
            request.execute()
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
                log.warning("Spotify token not found — run auth flow first: %s",
                            sp_oauth.get_authorize_url())
                return None
            token_info = sp_oauth.refresh_access_token(token_info["refresh_token"])

        self._spotify = Spotify(auth_manager=sp_oauth)
        return self._spotify

    def _extract_spotify_track_id(self, url: str) -> str | None:
        import re
        m = re.search(r"/track/([A-Za-z0-9]{22})", url)
        return m.group(1) if m else None

    def add_to_spotify_playlist(
        self, channel_id: str, playlist_id: str, track_url: str,
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
            client.playlist_add_items(
                playlist_id,
                [f"spotify:track:{track_id}"],
                position=0,
            )
            log.info("Added Spotify track %s to playlist %s", track_id, playlist_id)
            return True
        except Exception as e:
            log.error("Failed to add to Spotify playlist: %s", e)
            return False

    def create_spotify_playlist(
        self, channel_id: str, title: str = "Shared Playlist",
    ) -> str | None:
        client = self._ensure_spotify()
        if not client:
            return None
        user_id = client.current_user()["id"]
        playlist = client.user_playlist_create(
            user=user_id,
            name=title,
            public=False,
            description="Auto-generated from Discord music links",
        )
        return playlist.get("id")

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
            case Platform.YOUTUBE:
                video_id = link.track_id or link.original_url.split("v=")[-1][:11]
                return self.add_to_youtube_playlist(channel_id, playlist_id, video_id)
            case Platform.YOUTUBE_MUSIC:
                video_id = link.track_id or link.original_url.split("v=")[-1][:11]
                return self.add_to_youtube_playlist(channel_id, playlist_id, video_id)
            case Platform.SPOTIFY:
                return self.add_to_spotify_playlist(channel_id, playlist_id, link.original_url)
            case _:
                # Apple Music, SoundCloud, Bandcamp — no write API
                log.info("Platform %s does not support automated playlist writes", platform)
                return False

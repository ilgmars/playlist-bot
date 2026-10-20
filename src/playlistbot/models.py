"""Database models and schema for playlist-bot."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Platform(StrEnum):
    """Supported music platforms."""

    YOUTUBE = "youtube"
    YOUTUBE_MUSIC = "youtube_music"
    SPOTIFY = "spotify"
    APPLE_MUSIC = "apple_music"
    SOUND_CLOUD = "sound_cloud"
    BANDCAMP = "bandcamp"
    DEEZER = "deezer"
    TIDAL = "tidal"
    AMAZON_MUSIC = "amazon_music"
    QOBUZ = "qobuz"
    NAPSTER = "napster"
    PANDORA = "pandora"
    GENERIC = "generic"


class SongStatus(StrEnum):
    """Processing state for a song entry."""

    NEW = "new"
    ENRICHED = "enriched"
    IN_PLAYLIST = "in_playlist"
    ERROR = "error"


class SongLink(BaseModel):
    """A music link from a message, normalized to a canonical form."""

    model_config = ConfigDict(frozen=True)

    platform: Platform
    original_url: str
    canonical_url: str
    track_id: str | None = None
    added_by: str | None = None
    message_id: str | None = None


class Song(BaseModel):
    """A song collected from Discord, with all known links and metadata."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    artist: str
    album: str | None = None
    duration: int | None = None  # seconds
    status: SongStatus = SongStatus.NEW
    created_at: datetime
    updated_at: datetime
    links: list[SongLink] = []
    playlist_ids: dict[str, str] = {}  # platform -> playlist URL
    added_by: str | None = None
    message_id: str | None = None


class ThreadState(BaseModel):
    """Tracks the bot's latest message in a Discord thread for in-place updates."""

    model_config = ConfigDict(from_attributes=True)

    thread_id: str
    channel_id: str
    last_message_id: str | None = None
    last_edited_at: datetime | None = None
    latest_song_title: str | None = None
    latest_song_url: str | None = None
    total_songs: int = 0


class PlaylistState(BaseModel):
    """Tracks remote playlist IDs per channel/thread so we can append."""

    model_config = ConfigDict(from_attributes=True)

    channel_id: str
    platform: Platform
    playlist_id: str
    playlist_url: str
    last_synced: datetime | None = None

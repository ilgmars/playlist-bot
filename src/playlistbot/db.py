"""Database layer using aiosqlite with async context manager."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import aiosqlite

from .models import Platform, SongLink, SongStatus

DB_PATH = os.environ.get("DB_PATH", "/data/playlistbot.db")


SCHEMA = """
CREATE TABLE IF NOT EXISTS songs (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    artist          TEXT NOT NULL,
    album           TEXT,
    duration        INTEGER,
    status          TEXT NOT NULL DEFAULT 'new',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    links_json      TEXT NOT NULL DEFAULT '[]',
    playlist_ids_json TEXT NOT NULL DEFAULT '{}',
    added_by        TEXT,
    message_id      TEXT
);

CREATE TABLE IF NOT EXISTS threads (
    thread_id          TEXT PRIMARY KEY,
    channel_id         TEXT NOT NULL,
    last_message_id    TEXT,
    last_edited_at     TEXT,
    latest_song_title  TEXT,
    latest_song_url    TEXT,
    total_songs        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS playlists (
    channel_id   TEXT NOT NULL,
    platform     TEXT NOT NULL,
    playlist_id  TEXT NOT NULL,
    playlist_url TEXT NOT NULL,
    last_synced       TEXT,
    PRIMARY KEY (channel_id, platform)
);

CREATE TABLE IF NOT EXISTS dedup (
    canonical_url TEXT PRIMARY KEY,
    song_id       TEXT NOT NULL,
    added_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

# Index for ordering by newest first
INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_songs_created ON songs(created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_songs_status ON songs(status);",
    "CREATE INDEX IF NOT EXISTS idx_dedup_song ON dedup(song_id);",
]


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


@asynccontextmanager
async def get_db() -> AsyncIterator[aiosqlite.Connection]:
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.commit()
    try:
        yield conn
    finally:
        await conn.close()


async def init_db() -> None:
    """Create tables and indexes. Called once on startup."""
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(SCHEMA)
        for idx in INDEX_SQL:
            await conn.execute(idx)
        await conn.commit()


async def insert_song(
    title: str,
    artist: str,
    links: list[SongLink],
    added_by: str | None = None,
    message_id: str | None = None,
    album: str | None = None,
    duration: int | None = None,
    playlist_ids: dict[str, str] | None = None,
) -> str:
    """Insert a song and its dedup links. Returns the song id."""
    song_id = str(uuid.uuid4())
    now = _now()
    links_json = json.dumps(
        [
            {
                "platform": link.platform.value,
                "original_url": link.original_url,
                "canonical_url": link.canonical_url,
                "track_id": link.track_id,
                "added_by": link.added_by,
                "message_id": link.message_id,
            }
            for link in links
        ]
    )
    playlists_json = json.dumps(playlist_ids or {})

    async with get_db() as db:
        await db.execute(
            """INSERT INTO songs
            (id, title, artist, album, duration, status, created_at, updated_at,
             links_json, playlist_ids_json, added_by, message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                song_id,
                title,
                artist,
                album,
                duration,
                SongStatus.ENRICHED.value,
                now,
                now,
                links_json,
                playlists_json,
                added_by,
                message_id,
            ),
        )
        for link in links:
            await db.execute(
                "INSERT OR IGNORE INTO dedup (canonical_url, song_id, added_at) VALUES (?, ?, ?)",
                (link.canonical_url, song_id, now),
            )
        await db.commit()
    return song_id


async def is_known_link(canonical_url: str) -> bool:
    async with get_db() as db:
        rows = await db.execute_fetchall(
            "SELECT 1 FROM dedup WHERE canonical_url = ?", (canonical_url,)
        )
        return len(rows) > 0


async def get_songs_newest_first(limit: int = 50) -> list[dict]:
    async with get_db() as db:
        rows = await db.execute_fetchall(
            "SELECT id, title, artist, album, created_at, links_json, playlist_ids_json "
            "FROM songs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        songs = []
        for row in rows:
            links = json.loads(row["links_json"])
            playlists = json.loads(row["playlist_ids_json"])
            songs.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "artist": row["artist"],
                    "album": row["album"],
                    "created_at": row["created_at"],
                    "links": links,
                    "playlist_urls": playlists,
                }
            )
        return songs


async def save_thread_state(
    thread_id: str,
    channel_id: str,
    last_message_id: str | None,
    total_songs: int,
    latest_title: str | None = None,
    latest_url: str | None = None,
) -> None:
    async with get_db() as db:
        now = _now()
        await db.execute(
            """INSERT INTO threads (thread_id, channel_id, last_message_id, last_edited_at,
               latest_song_title, latest_song_url, total_songs)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(thread_id) DO UPDATE SET
                 channel_id = excluded.channel_id,
                 last_message_id = excluded.last_message_id,
                 last_edited_at = excluded.last_edited_at,
                 latest_song_title = excluded.latest_song_title,
                 latest_song_url = excluded.latest_song_url,
                 total_songs = excluded.total_songs""",
            (thread_id, channel_id, last_message_id, now, latest_title, latest_url, total_songs),
        )
        await db.commit()


async def get_thread_state(thread_id: str) -> dict | None:
    async with get_db() as db:
        rows = await db.execute_fetchall("SELECT * FROM threads WHERE thread_id = ?", (thread_id,))
        if not rows:
            return None
        return dict(rows[0])


async def save_playlist_state(
    channel_id: str,
    platform: Platform,
    playlist_id: str,
    playlist_url: str,
) -> None:
    async with get_db() as db:
        now = _now()
        await db.execute(
            """INSERT INTO playlists (channel_id, platform, playlist_id, playlist_url, last_synced)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(channel_id, platform) DO UPDATE SET
                 playlist_id = excluded.playlist_id,
                 playlist_url = excluded.playlist_url,
                 last_synced = excluded.last_synced""",
            (channel_id, platform.value, playlist_id, playlist_url, now),
        )
        await db.commit()


async def update_playlist_url(song_id: str, platform: Platform, url: str) -> None:
    async with get_db() as db:
        rows = await db.execute_fetchall(
            "SELECT playlist_ids_json FROM songs WHERE id = ?", (song_id,)
        )
        if not rows:
            return
        row = rows[0]
        playlists: dict[str, str] = json.loads(row["playlist_ids_json"])
        playlists[platform.value] = url
        await db.execute(
            "UPDATE songs SET playlist_ids_json = ? WHERE id = ?",
            (json.dumps(playlists), song_id),
        )
        await db.commit()

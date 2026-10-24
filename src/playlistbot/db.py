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
    message_id      TEXT,
    channel_id      TEXT,
    guild_id        TEXT
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

CREATE TABLE IF NOT EXISTS votes (
    song_id     TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    username    TEXT,
    avatar_url  TEXT,
    value       INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (song_id, user_id)
);

CREATE TABLE IF NOT EXISTS servers (
    guild_id            TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    icon_url            TEXT,
    allowed_channels    TEXT NOT NULL DEFAULT '[]',
    enabled_platforms   TEXT NOT NULL DEFAULT '[]',
    auto_sync_playlists INTEGER NOT NULL DEFAULT 1,
    thread_card_enabled INTEGER NOT NULL DEFAULT 1,
    playlist_prefix     TEXT NOT NULL DEFAULT 'Discord Playlist',
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS guild_channels (
    channel_id  TEXT PRIMARY KEY,
    guild_id    TEXT NOT NULL,
    name        TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_guilds (
    user_id     TEXT NOT NULL,
    guild_id    TEXT NOT NULL,
    guild_name  TEXT NOT NULL,
    guild_icon  TEXT,
    can_manage  INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (user_id, guild_id)
);
"""

# Indexes for fast lookups and ordering
INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_songs_created ON songs(created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_songs_status ON songs(status);",
    "CREATE INDEX IF NOT EXISTS idx_songs_channel ON songs(channel_id);",
    "CREATE INDEX IF NOT EXISTS idx_songs_guild_created ON songs(guild_id, created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_dedup_song ON dedup(song_id);",
    "CREATE INDEX IF NOT EXISTS idx_votes_song ON votes(song_id);",
    "CREATE INDEX IF NOT EXISTS idx_guild_channels_guild ON guild_channels(guild_id);",
    "CREATE INDEX IF NOT EXISTS idx_user_guilds_user ON user_guilds(user_id);",
]


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _serialize_links(links: list[SongLink]) -> str:
    return json.dumps(
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
        cols = await conn.execute_fetchall("PRAGMA table_info(songs)")
        col_names = {c["name"] for c in cols}
        if "channel_id" not in col_names:
            await conn.execute("ALTER TABLE songs ADD COLUMN channel_id TEXT")
        if "guild_id" not in col_names:
            await conn.execute("ALTER TABLE songs ADD COLUMN guild_id TEXT")
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
    channel_id: str | None = None,
    guild_id: str | None = None,
) -> str:
    """Insert a song and its dedup links. Returns the song id."""
    song_id = str(uuid.uuid4())
    now = _now()
    links_json = _serialize_links(links)
    playlists_json = json.dumps(playlist_ids or {})

    async with get_db() as db:
        await db.execute(
            """INSERT INTO songs
            (id, title, artist, album, duration, status, created_at, updated_at,
             links_json, playlist_ids_json, added_by, message_id, channel_id, guild_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                channel_id,
                guild_id,
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


async def _attach_votes(
    db: aiosqlite.Connection,
    songs: list[dict],
    current_user_id: str | None = None,
) -> None:
    """Attach vote_count and user_voted to each song dictionary in-place."""
    if not songs:
        return
    vote_rows = await db.execute_fetchall(
        "SELECT song_id, COUNT(*) AS cnt FROM votes GROUP BY song_id"
    )
    counts = {r["song_id"]: int(r["cnt"]) for r in vote_rows}
    voted_ids: set[str] = set()
    if current_user_id:
        u_rows = await db.execute_fetchall(
            "SELECT song_id FROM votes WHERE user_id = ?",
            (current_user_id,),
        )
        voted_ids = {r["song_id"] for r in u_rows}
    for song in songs:
        sid = song["id"]
        song["vote_count"] = counts.get(sid, 0)
        song["user_voted"] = sid in voted_ids


def _build_songs_query(
    channel_id: str | None,
    guild_id: str | None,
    limit: int,
) -> tuple[str, tuple]:
    cols = (
        "SELECT id, title, artist, album, duration, created_at, links_json, "
        "playlist_ids_json, added_by, channel_id, guild_id FROM songs "
    )
    order = "ORDER BY created_at DESC LIMIT ?"
    if channel_id and guild_id:
        return cols + "WHERE channel_id = ? AND guild_id = ? " + order, (
            channel_id,
            guild_id,
            limit,
        )
    if channel_id:
        return cols + "WHERE channel_id = ? " + order, (channel_id, limit)
    if guild_id:
        return cols + "WHERE guild_id = ? " + order, (guild_id, limit)
    return cols + order, (limit,)


async def get_songs_newest_first(
    limit: int = 50,
    channel_id: str | None = None,
    current_user_id: str | None = None,
    sort_by: str = "newest",
    guild_id: str | None = None,
) -> list[dict]:
    async with get_db() as db:
        sql, params = _build_songs_query(channel_id, guild_id, limit)
        rows = await db.execute_fetchall(sql, params)
        songs = [
            {
                "id": row["id"],
                "title": row["title"],
                "artist": row["artist"],
                "album": row["album"],
                "duration": row["duration"],
                "created_at": row["created_at"],
                "links": json.loads(row["links_json"]),
                "playlist_urls": json.loads(row["playlist_ids_json"]),
                "added_by": row["added_by"],
                "channel_id": row["channel_id"],
                "guild_id": row["guild_id"],
            }
            for row in rows
        ]
        await _attach_votes(db, songs, current_user_id=current_user_id)
        if sort_by == "votes":
            songs.sort(
                key=lambda s: (s.get("vote_count", 0), s.get("created_at", "")), reverse=True
            )
        return songs


async def toggle_vote(
    song_id: str,
    user_id: str,
    username: str | None = None,
    avatar_url: str | None = None,
) -> dict:
    """Toggle a user's upvote on a song and return the updated state."""
    async with get_db() as db:
        existing = await db.execute_fetchall(
            "SELECT 1 FROM votes WHERE song_id = ? AND user_id = ?",
            (song_id, user_id),
        )
        if existing:
            await db.execute(
                "DELETE FROM votes WHERE song_id = ? AND user_id = ?",
                (song_id, user_id),
            )
            voted = False
        else:
            await db.execute(
                "INSERT INTO votes (song_id, user_id, username, avatar_url, value, created_at) "
                "VALUES (?, ?, ?, ?, 1, ?)",
                (song_id, user_id, username, avatar_url, _now()),
            )
            voted = True
        await db.commit()
        count_rows = await db.execute_fetchall(
            "SELECT COUNT(*) AS cnt FROM votes WHERE song_id = ?",
            (song_id,),
        )
        count = int(count_rows[0]["cnt"]) if count_rows else 0
        return {"song_id": song_id, "voted": voted, "vote_count": count}


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


async def get_playlists(channel_id: str | None = None) -> list[dict]:
    """Return stored playlist records, optionally filtered by channel_id."""
    async with get_db() as db:
        if channel_id:
            rows = await db.execute_fetchall(
                "SELECT channel_id, platform, playlist_id, playlist_url, last_synced "
                "FROM playlists WHERE channel_id = ? ORDER BY platform",
                (channel_id,),
            )
        else:
            rows = await db.execute_fetchall(
                "SELECT channel_id, platform, playlist_id, playlist_url, last_synced "
                "FROM playlists ORDER BY channel_id, platform"
            )
        return [dict(r) for r in rows]


async def get_channels(guild_id: str | None = None) -> list[str]:
    """Return distinct non-empty channel_ids, optionally scoped to a guild_id."""
    async with get_db() as db:
        if guild_id:
            rows = await db.execute_fetchall(
                "SELECT DISTINCT channel_id FROM ("
                "  SELECT channel_id FROM songs "
                "  WHERE guild_id = ? AND channel_id IS NOT NULL AND channel_id != '' "
                "  UNION "
                "  SELECT channel_id FROM guild_channels WHERE guild_id = ?"
                ") ORDER BY channel_id",
                (guild_id, guild_id),
            )
            return [r["channel_id"] for r in rows]
        rows = await db.execute_fetchall(
            "SELECT DISTINCT channel_id FROM ("
            "  SELECT channel_id FROM songs WHERE channel_id IS NOT NULL AND channel_id != '' "
            "  UNION "
            "  SELECT channel_id FROM threads WHERE channel_id IS NOT NULL AND channel_id != '' "
            "  UNION "
            "  SELECT channel_id FROM playlists WHERE channel_id IS NOT NULL AND channel_id != ''"
            ") ORDER BY channel_id"
        )
        return [r["channel_id"] for r in rows]


async def get_stats(channel_id: str | None = None, guild_id: str | None = None) -> dict:
    """Compute summary statistics for songs, platforms, links, and contributors."""
    songs = await get_songs_newest_first(limit=500, channel_id=channel_id, guild_id=guild_id)
    platforms: dict[str, int] = {}
    contributors: dict[str, int] = {}
    total_links = 0
    for song in songs:
        added_by = song.get("added_by")
        if added_by:
            contributors[added_by] = contributors.get(added_by, 0) + 1
        for link in song.get("links", []):
            plat = link.get("platform", "unknown")
            platforms[plat] = platforms.get(plat, 0) + 1
            total_links += 1
    return {
        "total_songs": len(songs),
        "total_links": total_links,
        "total_votes": sum(int(s.get("vote_count", 0)) for s in songs),
        "platforms": platforms,
        "contributors": contributors,
    }

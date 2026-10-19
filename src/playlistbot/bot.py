"""Discord bot entry point — listens for music links and manages playists.

Key design: the bot posts a single 'card' message in a thread per channel.
On each new song, it edits that message in place (no duplicate messages).
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import discord
from discord import Message, TextChannel, Thread
from discord.ext import commands

from .db import (
    get_thread_state,
    init_db,
    insert_song,
    is_known_link,
    save_playlist_state,
    save_thread_state,
    update_playlist_url,
)
from .link_parser import ParsedLink, Platform, extract_links
from .metadata import extract_metadata
from .models import SongLink
from .playlist import PlaylistManager

log = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
playlist_mgr = PlaylistManager()


def _build_card_embed(
    song_title: str,
    song_artist: str,
    all_links: list[dict],
    total: int,
) -> discord.Embed:
    """Build the embed shown in the thread card message."""
    embed = discord.Embed(
        title="Latest song added",
        color=0x1DB954,
        timestamp=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    embed.add_field(name="Track", value=f"**{song_title}** — {song_artist}", inline=False)
    links_text = "\n".join(
        f"[{link['platform'].capitalize()}]({link['original_url']})"
        for link in all_links
    )
    embed.add_field(name="Links", value=links_text or "No links available", inline=False)
    embed.set_footer(text=f"{total} songs collected")
    return embed


async def _get_or_create_thread(channel: TextChannel, song_title: str) -> Thread:
    """Look for an existing thread or create one called 'playlist-track'."""
    existing = None
    for thread in channel.threads:
        if thread.name == "playlist-track" and not thread.archived:
            existing = thread
            break
    if existing:
        return existing

    thread = await channel.create_thread(
        name="playlist-track",
        auto_archive_duration=4320,
        reason="Auto-created playlist tracking thread",
    )
    return thread


async def _send_or_update_card(thread: Thread, embed: discord.Embed) -> Message:
    """Either edit the stored bot message, or send a new one."""
    state = await get_thread_state(thread.id)
    if state and state.get("last_message_id"):
        try:
            msg = await thread.fetch_message(int(state["last_message_id"]))
            await msg.edit(embed=embed)
            return msg
        except (discord.NotFound, discord.Forbidden):
            log.warning("Could not edit bot message %s, will send new", state["last_message_id"])

    msg = await thread.send(embed=embed)
    return msg


@bot.event
async def on_ready():
    log.info("Playlist bot connected as %s", bot.user)


@bot.event
async def on_message(message: Message):
    if message.author.bot:
        return

    # Don't process commands here — let the command processor handle "!" prefix
    await bot.process_commands(message)

    # Parse links from the message content
    links = extract_links(message.content)
    if not links:
        return

    # Check for duplicates
    new_links = []
    for link in links:
        if await is_known_link(link.canonical_url):
            continue
        new_links.append(link)

    if not new_links:
        return

    channel: TextChannel = message.channel  # type: ignore[assignment]

    # Enrich metadata in parallel
    metadata_tasks = [extract_metadata(link) for link in new_links]
    metadatas = await asyncio.gather(*metadata_tasks)

    # Create or reuse thread
    thread = await _get_or_create_thread(channel, "playlist-track")

    # Insert each new song and collect the result
    saved_songs: list[dict[str, Any]] = []
    for link, meta in zip(new_links, metadatas, strict=True):
        if meta is None:
            log.warning("Could not extract metadata for %s", link.original_url)
            continue

        song_id = await insert_song(
            title=meta.title,
            artist=meta.artist,
            album=meta.album,
            duration=meta.duration_ms,
            links=[_parsed_to_model(link, message)],
            added_by=str(message.author),
            message_id=str(message.id),
        )

        # Try to add to playlists
        for platform in [Platform.YOUTUBE, Platform.SPOTIFY]:
            playlist_id = await _get_playlist_id(str(channel.id), platform)
            if playlist_id:
                success = playlist_mgr.add_to_playlist(
                    platform=platform,
                    channel_id=str(channel.id),
                    playlist_id=playlist_id,
                    link=link,
                )
                if success:
                    playlist_url = await _get_playlist_url(str(channel.id), platform)
                    if playlist_url:
                        await update_playlist_url(song_id, platform, playlist_url)

        saved_songs.append({
            "id": song_id,
            "title": meta.title,
            "artist": meta.artist,
            "links": [_link_to_dict(link, message)],
        })

    if not saved_songs:
        return

    # Use the latest song for the card
    latest = saved_songs[-1]
    total = await _get_song_count()
    embed = _build_card_embed(
        song_title=latest["title"],
        song_artist=latest["artist"],
        all_links=latest["links"],
        total=total,
    )

    msg = await _send_or_update_card(thread, embed)

    # Update thread state — single source of truth for last bot message
    await save_thread_state(
        thread_id=thread.id,
        channel_id=str(channel.id),
        last_message_id=str(msg.id),
        total_songs=total,
        latest_title=latest["title"],
        latest_url=latest["links"][0]["original_url"] if latest["links"] else None,
    )


def _parsed_to_model(link: ParsedLink, message: Message) -> SongLink:
    """Convert a ParsedLink to the DB SongLink model."""
    from .models import SongLink
    return SongLink(
        platform=link.platform,
        original_url=link.original_url,
        canonical_url=link.canonical_url,
        track_id=link.track_id,
        added_by=str(message.author),
        message_id=str(message.id),
    )


def _link_to_dict(link: ParsedLink, message: Message) -> dict:
    return {
        "platform": link.platform.value,
        "original_url": link.original_url,
        "canonical_url": link.canonical_url,
        "track_id": link.track_id,
        "added_by": str(message.author),
        "message_id": str(message.id),
    }


async def _get_playlist_id(channel_id: str, platform: Platform) -> str | None:
    """Check our DB for an existing playlist ID for this channel/platform."""
    async with __import__("playlistbot.db", fromlist=["get_db"]).get_db() as db:
        rows = await db.execute_fetchall(
            "SELECT playlist_id FROM playlists WHERE channel_id = ? AND platform = ?",
            (channel_id, platform.value),
        )
        if rows:
            return rows[0]["playlist_id"]

    # No playlist yet — create one
    if platform == Platform.SPOTIFY:
        pid = playlist_mgr.create_spotify_playlist(channel_id)
    elif platform == Platform.YOUTUBE:
        pid = playlist_mgr._get_or_create_youtube_playlist(channel_id)
    else:
        pid = None

    if pid:
        url = f"https://open.spotify.com/playlist/{pid}" if platform == Platform.SPOTIFY else f"https://youtube.com/playlist?list={pid}"
        await save_playlist_state(channel_id, platform, pid, url)
    return pid


async def _get_playlist_url(channel_id: str, platform: Platform) -> str | None:
    async with __import__("playlistbot.db", fromlist=["get_db"]).get_db() as db:
        rows = await db.execute_fetchall(
            "SELECT playlist_url FROM playlists WHERE channel_id = ? AND platform = ?",
            (channel_id, platform.value),
        )
        return rows[0]["playlist_url"] if rows else None


async def _get_song_count() -> int:
    async with __import__("playlistbot.db", fromlist=["get_db"]).get_db() as db:
        rows = await db.execute_fetchall("SELECT COUNT(*) as c FROM songs", ())
        return rows[0]["c"] if rows else 0


async def _create_missing_playlists(channel_id: str) -> None:
    """Ensure playlists exist on each platform for this channel."""
    for platform in (Platform.YOUTUBE, Platform.SPOTIFY):
        pid = await _get_playlist_id(channel_id, platform)
        if pid:
            url = (
                f"https://youtube.com/playlist?list={pid}"
                if platform == Platform.YOUTUBE
                else f"https://open.spotify.com/playlist/{pid}"
            )
            await save_playlist_state(channel_id, platform, pid, url)


def run_bot(token: str) -> None:
    """Entry point for running the bot."""
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    channel_id = str(os.environ.get("DISCORD_CHANNEL_ID", ""))
    guild_id = str(os.environ.get("DISCORD_GUILD_ID", ""))
    loop.run_until_complete(init_db())
    loop.run_until_complete(_create_missing_playlists(channel_id))
    loop.run_until_complete(_create_missing_playlists(guild_id))
    bot.run(token)

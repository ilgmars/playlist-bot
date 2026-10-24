"""Discord bot entry point — listens for music links and manages playists.

Key design: the bot posts a single 'card' message in a thread per channel.
On each new song, it edits that message in place (no duplicate messages).
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from datetime import UTC, datetime
from typing import Any

import discord
from discord import Interaction, Message, TextChannel, Thread
from discord.ext import commands

from .db import (
    get_db,
    get_playlists,
    get_songs_newest_first,
    get_stats,
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
from .playlist import PlaylistManager, generate_m3u

log = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
playlist_mgr = PlaylistManager()


def _get_allowed_channels() -> set[str]:
    """Return configured allowed Discord channel IDs (empty set means all channels)."""
    raw = os.environ.get("DISCORD_CHANNEL_IDS", "")
    single = os.environ.get("DISCORD_CHANNEL_ID", "")
    combined = f"{raw},{single}" if raw and single else (raw or single)
    return {cid.strip() for cid in combined.split(",") if cid.strip()}


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
        timestamp=datetime.now(UTC),
    )
    embed.add_field(name="Track", value=f"**{song_title}** — {song_artist}", inline=False)
    links_text = "\n".join(
        f"[{link['platform'].capitalize()}]({link['original_url']})" for link in all_links
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


async def _save_and_sync_songs(
    new_links: list[ParsedLink],
    metadatas: list[Any],
    message: Message,
    channel_id: str,
) -> list[dict[str, Any]]:
    """Persist new songs in DB and sync them to channel playlists."""
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
            channel_id=channel_id,
        )

        for platform in (Platform.YOUTUBE, Platform.SPOTIFY):
            playlist_id = await _get_playlist_id(channel_id, platform)
            if playlist_id and await asyncio.to_thread(
                playlist_mgr.add_to_playlist,
                platform=platform,
                channel_id=channel_id,
                playlist_id=playlist_id,
                link=link,
            ):
                playlist_url = await _get_playlist_url(channel_id, platform)
                if playlist_url:
                    await update_playlist_url(song_id, platform, playlist_url)

        saved_songs.append(
            {
                "id": song_id,
                "title": meta.title,
                "artist": meta.artist,
                "links": [_link_to_dict(link, message)],
            }
        )
    return saved_songs


@bot.event
async def on_ready():
    tree = getattr(bot, "tree", None)
    if tree is not None and getattr(bot, "application_id", None):
        await tree.sync()
    log.info("Playlist bot connected as %s", bot.user)


@bot.event
async def on_message(message: Message):
    if message.author.bot:
        return

    allowed = _get_allowed_channels()
    if allowed and str(message.channel.id) not in allowed:
        return

    await bot.process_commands(message)

    links = extract_links(message.content)
    if not links:
        return

    new_links = [link for link in links if not await is_known_link(link.canonical_url)]
    if not new_links:
        return

    channel: TextChannel = message.channel  # type: ignore[assignment]
    metadatas = await asyncio.gather(*(extract_metadata(link) for link in new_links))
    thread = await _get_or_create_thread(channel, "playlist-track")
    saved_songs = await _save_and_sync_songs(new_links, metadatas, message, str(channel.id))
    if not saved_songs:
        return

    latest = saved_songs[-1]
    total = await _get_song_count()
    embed = _build_card_embed(
        song_title=latest["title"],
        song_artist=latest["artist"],
        all_links=latest["links"],
        total=total,
    )
    msg = await _send_or_update_card(thread, embed)
    await save_thread_state(
        thread_id=thread.id,
        channel_id=str(channel.id),
        last_message_id=str(msg.id),
        total_songs=total,
        latest_title=latest["title"],
        latest_url=latest["links"][0]["original_url"] if latest["links"] else None,
    )


@bot.tree.command(name="playlist-create", description="Create or ensure playlists for this channel")
async def playlist_create_cmd(interaction: Interaction) -> None:
    """Slash command to manually trigger playlist creation for the current channel."""
    channel_id = str(interaction.channel_id or "")
    await _create_missing_playlists(channel_id)
    playlists = await get_playlists(channel_id)
    if not playlists:
        await interaction.response.send_message(
            f"Playlist setup triggered for channel `{channel_id}` (no API credentials configured).",
            ephemeral=True,
        )
        return
    lines = [
        f"• **{p['platform'].capitalize()}**: {p['playlist_url'] or p['playlist_id']}"
        for p in playlists
    ]
    await interaction.response.send_message(
        "Playlists ready for this channel:\n" + "\n".join(lines),
        ephemeral=True,
    )


@bot.tree.command(name="playlist-export", description="Export channel playlist as an .m3u file")
async def playlist_export_cmd(interaction: Interaction) -> None:
    """Slash command to export collected songs as an Extended M3U attachment."""
    channel_id = str(interaction.channel_id) if interaction.channel_id else None
    songs = await get_songs_newest_first(limit=500, channel_id=channel_id)
    if not songs:
        songs = await get_songs_newest_first(limit=500)
    m3u_content = generate_m3u(songs, title=f"Discord Playlist #{channel_id or 'all'}")
    buf = io.BytesIO(m3u_content.encode("utf-8"))
    await interaction.response.send_message(
        f"Exported **{len(songs)}** tracks as `.m3u`:",
        file=discord.File(fp=buf, filename="playlist.m3u"),
        ephemeral=True,
    )


@bot.tree.command(name="playlist-links", description="Show playlist links and recent tracks")
async def playlist_links_cmd(interaction: Interaction) -> None:
    """Slash command to display current playlist URLs and latest shared tracks."""
    channel_id = str(interaction.channel_id) if interaction.channel_id else None
    playlists = await get_playlists(channel_id)
    songs = await get_songs_newest_first(limit=5, channel_id=channel_id)
    if not songs and channel_id:
        songs = await get_songs_newest_first(limit=5)

    parts: list[str] = []
    if playlists:
        parts.append("**Playlists:**")
        for pl in playlists:
            parts.append(f"• {pl['platform'].capitalize()}: {pl['playlist_url']}")
    if songs:
        parts.append("\n**Recent Tracks:**")
        for song in songs:
            url = song["links"][0]["original_url"] if song.get("links") else ""
            parts.append(f"• **{song['title']}** — {song['artist']} ({url})")
    if not parts:
        parts.append("No playlists or songs recorded yet.")
    await interaction.response.send_message("\n".join(parts), ephemeral=True)


@bot.tree.command(name="playlist-stats", description="Show song count and platform breakdown")
async def playlist_stats_cmd(interaction: Interaction) -> None:
    """Slash command to display song, link, platform, and contributor statistics."""
    channel_id = str(interaction.channel_id) if interaction.channel_id else None
    stats = await get_stats(channel_id=channel_id)
    if stats["total_songs"] == 0 and channel_id:
        stats = await get_stats(channel_id=None)

    breakdown = ", ".join(f"{plat.capitalize()}: {cnt}" for plat, cnt in stats["platforms"].items())
    contrib_count = len(stats["contributors"]) if isinstance(stats["contributors"], dict) else 0
    msg = (
        f"**Playlist Stats**\n"
        f"• Songs: **{stats['total_songs']}**\n"
        f"• Links: **{stats['total_links']}**\n"
        f"• Contributors: **{contrib_count}**\n"
        f"• Platforms: {breakdown or 'None'}"
    )
    await interaction.response.send_message(msg, ephemeral=True)


def _parsed_to_model(link: ParsedLink, message: Message) -> SongLink:
    """Convert a ParsedLink to the DB SongLink model."""
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
    async with get_db() as db:
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
        url = (
            f"https://open.spotify.com/playlist/{pid}"
            if platform == Platform.SPOTIFY
            else f"https://youtube.com/playlist?list={pid}"
        )
        await save_playlist_state(channel_id, platform, pid, url)
    return pid


async def _get_playlist_url(channel_id: str, platform: Platform) -> str | None:
    async with get_db() as db:
        rows = await db.execute_fetchall(
            "SELECT playlist_url FROM playlists WHERE channel_id = ? AND platform = ?",
            (channel_id, platform.value),
        )
        return rows[0]["playlist_url"] if rows else None


async def _get_song_count() -> int:
    async with get_db() as db:
        rows = await db.execute_fetchall("SELECT COUNT(*) as c FROM songs", ())
        return rows[0]["c"] if rows else 0


async def _create_missing_playlists(channel_id: str) -> None:
    """Ensure playlists exist on each platform for this channel."""
    if not channel_id:
        return
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
    for extra_id in os.environ.get("DISCORD_CHANNEL_IDS", "").split(","):
        if extra_id.strip():
            loop.run_until_complete(_create_missing_playlists(extra_id.strip()))
    bot.run(token)

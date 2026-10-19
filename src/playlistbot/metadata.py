"""Metadata extraction for music links.

Uses yt-dlp to fetch YouTube metadata, and direct HTTP calls for Spotify / Apple Music.
Returns a normalized TrackInfo dataclass.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx
import yt_dlp

from .link_parser import ParsedLink, Platform

log = logging.getLogger(__name__)

_SPOTIFY_API_BASE = "https://api.spotify.com/v1"


@dataclass(slots=True)
class TrackInfo:
    """Normalized track info from any source."""

    title: str
    artist: str
    album: str | None = None
    duration_ms: int | None = None
    isrc: str | None = None
    source_url: str = ""
    source_platform: Platform = Platform.YOUTUBE


@dataclass(slots=True)
class MetadataResult:
    """One track can produce multiple platform links."""

    primary: TrackInfo
    links: dict[str, str]


_yt_dlp_opts: dict[str, Any] = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "extract_flat": False,
}


async def fetch_youtube_metadata(url: str) -> TrackInfo | None:
    """Use yt-dlp in a background thread to extract metadata."""

    def _extract() -> dict[str, Any] | None:
        with yt_dlp.YoutubeDL(_yt_dlp_opts) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        info = await asyncio.to_thread(_extract)
    except Exception as e:
        log.warning("yt-dlp failed for %s: %s", url, e)
        return None

    return TrackInfo(
        title=info.get("title", "Unknown"),
        artist=info.get("artist") or (info.get("uploader") or "Unknown"),
        album=info.get("album"),
        duration_ms=int(info.get("duration", 0) * 1000) if info.get("duration") else None,
        source_url=url,
        source_platform=Platform.YOUTUBE,
    )


async def fetch_spotify_metadata(
    url: str, token: str | None = None,
) -> TrackInfo | None:
    """Fetch Spotify metadata from the track URL.

    Uses the public oEmbed endpoint (no auth needed) as a fallback,
    or the full API with a bearer token for richer data.
    """
    match = re.search(r"/track/([A-Za-z0-9]{22})", url)
    if not match:
        return None

    track_id = match.group(1)
    spotify_url = f"https://open.spotify.com/track/{track_id}"

    if token:
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{_SPOTIFY_API_BASE}/tracks/{track_id}",
                headers=headers,
            )
        if resp.status_code == 200:
            data: dict[str, Any] = resp.json()
            artists = [a["name"] for a in data.get("artists", [])]
            return TrackInfo(
                title=data.get("name", "Unknown"),
                artist=", ".join(artists) if artists else "Unknown",
                album=data.get("album", {}).get("name"),
                duration_ms=data.get("duration_ms"),
                isrc=data.get("external_ids", {}).get("isrc"),
                source_url=spotify_url,
                source_platform=Platform.SPOTIFY,
            )

    # Fallback: scrape oEmbed for basic info
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://open.spotify.com/oembed?url={spotify_url}"
            )
        if resp.status_code == 200:
            data = resp.json()
            title = data.get("title", "")
            parts = title.rsplit(" - ", 1)
            if len(parts) == 2:
                return TrackInfo(
                    title=parts[0],
                    artist=parts[1],
                    source_url=spotify_url,
                    source_platform=Platform.SPOTIFY,
                )
    except Exception as e:
        log.debug("Spotify oEmbed failed: %s", e)

    return TrackInfo(
        title="Unknown (Spotify)",
        artist="Unknown",
        source_url=spotify_url,
        source_platform=Platform.SPOTIFY,
    )


async def fetch_apple_music_metadata(url: str) -> TrackInfo | None:
    """Scrape Apple Music metadata from the public page."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Version/17.0 Safari/537.36"
        )
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=headers, follow_redirects=True)
        if resp.status_code != 200:
            return None
        text = resp.text
    except Exception as e:
        log.debug("Apple Music request failed: %s", e)
        return None

    # Apple Music embeds JSON in a <script> tag
    script_match = re.search(r"<script[^>]*>(.+?)</script>", text, re.DOTALL)
    if script_match:
        scripts = script_match.group(1)
        name_match = re.search(r'"name"\s*:\s*"([^"]+)"', scripts)
        artist_match = re.search(r'"artistName"\s*:\s*"([^"]+)"', scripts)
        if name_match and artist_match:
            return TrackInfo(
                title=name_match.group(1),
                artist=artist_match.group(1),
                source_url=url,
                source_platform=Platform.APPLE_MUSIC,
            )

    # Fallback: parse <title> — typical format: "Song — Artist | Apple Music"
    title_match = re.search(r"<title>([^<]+)</title>", text)
    if title_match:
        raw = title_match.group(1)
        parts = raw.split(" — ")
        if len(parts) >= 2:
            return TrackInfo(
                title=parts[0].strip(),
                artist=parts[1].replace("| Apple Music", "").strip(),
                source_url=url,
                source_platform=Platform.APPLE_MUSIC,
            )
    return None


async def fetch_soundcloud_metadata(url: str) -> TrackInfo | None:
    """Use SoundCloud's oEmbed endpoint."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://soundcloud.com/oembed",
                params={"format": "json", "url": url},
            )
        if resp.status_code == 200:
            data = resp.json()
            title = data.get("title", "")
            parts = title.split(" - ", 1)
            if len(parts) == 2:
                artist, name = parts
            else:
                artist, name = "Unknown", title
            return TrackInfo(
                title=name,
                artist=artist,
                source_url=url,
                source_platform=Platform.SOUND_CLOUD,
            )
    except Exception as e:
        log.debug("SoundCloud oEmbed failed: %s", e)
    return TrackInfo(
        title="Unknown (SoundCloud)",
        artist="Unknown",
        source_url=url,
        source_platform=Platform.SOUND_CLOUD,
    )


async def fetch_bandcamp_metadata(url: str) -> TrackInfo | None:
    """Scrape Bandcamp page for track metadata."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; PlaylistBot/0.1)"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return None
        text = resp.text
    except Exception as e:
        log.debug("Bandcamp request failed: %s", e)
        return None

    m = re.search(r"trackinfo\s*:\s*(\[.+?\])", text)
    if m:
        try:
            info = json.loads(m.group(1))
        except json.JSONDecodeError:
            return None
        if info and isinstance(info, list) and len(info) > 0:
            track = info[0]
            return TrackInfo(
                title=track.get("title", "Unknown"),
                artist=track.get("artist", "Unknown"),
                album=track.get("album_title"),
                duration_ms=int(track.get("duration", 0) * 1000)
                if track.get("duration")
                else None,
                source_url=url,
                source_platform=Platform.BANDCAMP,
            )
    return None


async def extract_metadata(link: ParsedLink) -> TrackInfo | None:
    """Dispatch to the right metadata fetcher based on platform."""
    match link.platform:
        case Platform.YOUTUBE | Platform.YOUTUBE_MUSIC:
            return await fetch_youtube_metadata(link.original_url)
        case Platform.SPOTIFY:
            token = os.environ.get("SPOTIFY_ACCESS_TOKEN")
            return await fetch_spotify_metadata(link.original_url, token)
        case Platform.APPLE_MUSIC:
            return await fetch_apple_music_metadata(link.original_url)
        case Platform.SOUND_CLOUD:
            return await fetch_soundcloud_metadata(link.original_url)
        case Platform.BANDCAMP:
            return await fetch_bandcamp_metadata(link.original_url)
    return None

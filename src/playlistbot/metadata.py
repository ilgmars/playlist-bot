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


def _parse_retry_after(header_val: str | None, attempt: int, base_delay: float = 0.5) -> float:
    """Compute retry delay from Retry-After header or exponential backoff."""
    if header_val is not None:
        try:
            return max(0.0, float(header_val))
        except ValueError:
            pass
    return float(base_delay * (2**attempt))


async def _http_get_with_backoff(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
    follow_redirects: bool = False,
    max_retries: int = 3,
    base_delay: float = 0.5,
) -> httpx.Response:
    """Perform an HTTP GET with exponential backoff on 429 Too Many Requests."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            url,
            headers=headers,
            params=params,
            follow_redirects=follow_redirects,
        )
        for attempt in range(max_retries - 1):
            if resp.status_code != 429:
                break
            delay = _parse_retry_after(resp.headers.get("Retry-After"), attempt, base_delay)
            log.warning("Rate limited (429) on %s, retrying in %.2fs", url, delay)
            await asyncio.sleep(delay)
            resp = await client.get(
                url,
                headers=headers,
                params=params,
                follow_redirects=follow_redirects,
            )
        return resp


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
    if info is None:
        return None

    return TrackInfo(
        title=info.get("title", "Unknown"),
        artist=info.get("artist") or (info.get("uploader") or "Unknown"),
        album=info.get("album"),
        duration_ms=int(info.get("duration", 0) * 1000) if info.get("duration") else None,
        source_url=url,
        source_platform=Platform.YOUTUBE,
    )


async def _fetch_spotify_api(track_id: str, spotify_url: str, bearer: str) -> TrackInfo | None:
    """Fetch rich Spotify metadata via the Web API using a bearer credential."""
    headers = {"Authorization": f"Bearer {bearer}"}
    resp = await _http_get_with_backoff(f"{_SPOTIFY_API_BASE}/tracks/{track_id}", headers=headers)
    if resp.status_code != 200:
        return None
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


async def fetch_spotify_metadata(
    url: str,
    token: str | None = None,  # noqa: S107 - optional OAuth bearer
) -> TrackInfo | None:
    """Fetch Spotify metadata from the track URL."""
    match = re.search(r"/track/([A-Za-z0-9]{22})", url)
    if not match:
        return None

    track_id = match.group(1)
    spotify_url = f"https://open.spotify.com/track/{track_id}"

    if token:  # bearer credential provided
        info = await _fetch_spotify_api(track_id, spotify_url, token)
        if info is not None:
            return info

    # Fallback: scrape oEmbed for basic info
    try:
        resp = await _http_get_with_backoff(f"https://open.spotify.com/oembed?url={spotify_url}")
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
        resp = await _http_get_with_backoff(url, headers=headers, follow_redirects=True)
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
        resp = await _http_get_with_backoff(
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
        resp = await _http_get_with_backoff(url, headers=headers)
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
                duration_ms=int(track.get("duration", 0) * 1000) if track.get("duration") else None,
                source_url=url,
                source_platform=Platform.BANDCAMP,
            )
    return None


async def fetch_deezer_metadata(url: str) -> TrackInfo | None:
    """Use Deezer's public API (no auth needed for basic track info)."""
    m = re.search(r"/track/(\d+)", url)
    if not m:
        return None
    track_id = m.group(1)
    try:
        resp = await _http_get_with_backoff(f"https://api.deezer.com/track/{track_id}")
        if resp.status_code == 200:
            data = resp.json()
            return TrackInfo(
                title=data.get("title", "Unknown"),
                artist=data.get("artist", {}).get("name", "Unknown"),
                album=data.get("album", {}).get("title"),
                duration_ms=data.get("duration", 0) * 1000,
                isrc=data.get("isrc"),
                source_url=url,
                source_platform=Platform.DEEZER,
            )
    except Exception as e:
        log.debug("Deezer API failed: %s", e)
    return TrackInfo(
        title="Unknown (Deezer)",
        artist="Unknown",
        source_url=url,
        source_platform=Platform.DEEZER,
    )


async def fetch_deezer_oembed(url: str) -> TrackInfo | None:
    """Fetch Deezer metadata via public oEmbed endpoint."""
    try:
        resp = await _http_get_with_backoff(
            "https://api.deezer.com/oembed",
            params={"url": url, "format": "json"},
        )
        if resp.status_code == 200:
            data = resp.json()
            return TrackInfo(
                title=data.get("title", "Unknown"),
                artist=data.get("author_name", "Unknown"),
                source_url=url,
                source_platform=Platform.DEEZER,
            )
    except Exception as e:
        log.debug("Deezer oEmbed failed: %s", e)
    return None


async def fetch_tidal_metadata(url: str) -> TrackInfo | None:
    """Tidal requires OAuth; fallback to title scrape."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; PlaylistBot/0.1)"}
    try:
        resp = await _http_get_with_backoff(url, headers=headers, follow_redirects=True)
        if resp.status_code != 200:
            return None
        text = resp.text
    except Exception as e:
        log.debug("Tidal request failed: %s", e)
        return None

    # Try to extract from page title
    m = re.search(r"<title>([^<]+)</title>", text, re.IGNORECASE)
    if m:
        raw = m.group(1)
        parts = raw.split(" - ", 1)
        if len(parts) == 2:
            return TrackInfo(
                title=parts[0].strip(),
                artist=parts[1].strip(),
                source_url=url,
                source_platform=Platform.TIDAL,
            )
    return TrackInfo(
        title="Unknown (Tidal)",
        artist="Unknown",
        source_url=url,
        source_platform=Platform.TIDAL,
    )


async def fetch_amazon_music_metadata(url: str) -> TrackInfo | None:
    """Amazon Music requires auth; fallback."""
    return TrackInfo(
        title="Unknown (Amazon Music)",
        artist="Unknown",
        source_url=url,
        source_platform=Platform.AMAZON_MUSIC,
    )


async def fetch_qobuz_metadata(url: str) -> TrackInfo | None:
    """Qobuz requires OAuth; fallback."""
    return TrackInfo(
        title="Unknown (Qobuz)",
        artist="Unknown",
        source_url=url,
        source_platform=Platform.QOBUZ,
    )


async def fetch_napster_metadata(url: str) -> TrackInfo | None:
    """Napster requires OAuth; fallback."""
    return TrackInfo(
        title="Unknown (Napster)",
        artist="Unknown",
        source_url=url,
        source_platform=Platform.NAPSTER,
    )


async def fetch_pandora_metadata(url: str) -> TrackInfo | None:
    """Pandora US-only; fallback."""
    return TrackInfo(
        title="Unknown (Pandora)",
        artist="Unknown",
        source_url=url,
        source_platform=Platform.PANDORA,
    )


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
        case Platform.DEEZER:
            return await fetch_deezer_metadata(link.original_url)
        case Platform.TIDAL:
            return await fetch_tidal_metadata(link.original_url)
        case Platform.AMAZON_MUSIC:
            return await fetch_amazon_music_metadata(link.original_url)
        case Platform.QOBUZ:
            return await fetch_qobuz_metadata(link.original_url)
        case Platform.NAPSTER:
            return await fetch_napster_metadata(link.original_url)
        case Platform.PANDORA:
            return await fetch_pandora_metadata(link.original_url)
    return None

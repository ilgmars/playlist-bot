"""Parse and normalize music links from Discord messages.

Supports YouTube, YouTube Music, Spotify, Apple Music, SoundCloud, and Bandcamp.
Each platform has a regex pattern and a normalizer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class Platform(StrEnum):
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


@dataclass(frozen=True)
class ParsedLink:
    platform: Platform
    original_url: str
    canonical_url: str
    track_id: str | None = None
    # Normalized artist+title for dedup; None if not extractable from the URL
    normalized_query: str | None = None


# ---------------------------------------------------------------------------
# Regex patterns.  Each tuple: (platform, compiled_regex, canonical_template)
# ---------------------------------------------------------------------------

_PATTERNS: list[tuple[Platform, re.Pattern[str], str]] = [
    # YouTube — watch?v=VIDEO_ID  OR  youtu.be/VIDEO_ID
    (Platform.YOUTUBE, re.compile(
        r'https?://(?:www\.)?youtube\.com/watch\?v=([A-Za-z0-9_-]{11})'
        r'|https?://youtu\.be/([A-Za-z0-9_-]{11})'
    ), 'https://www.youtube.com/watch?v={id}'),
    # YouTube Music
    (Platform.YOUTUBE_MUSIC, re.compile(
        r'https?://music\.youtube\.com/watch\?v=([A-Za-z0-9_-]{11})'
        r'|https?://(?:www\.)?youtube\.com/watch\?.*list=(RD)?[A-Za-z0-9_-]+.*v=([A-Za-z0-9_-]{11})'
    ), 'https://music.youtube.com/watch?v={id}'),
    # Spotify — track, album, artist, playlist, episode
    (Platform.SPOTIFY, re.compile(
        r'https?://(?:open\.spotify\.com|spotify\.com)/'
        r'(track|album|artist|playlist|episode)/'
        r'([A-Za-z0-9]{22})'
    ), 'https://open.spotify.com/{kind}/{id}'),
    # Apple Music
    (Platform.APPLE_MUSIC, re.compile(
        r'https?://music\.apple\.com/(?:[^/]+/)?'
        r'(?:album|artist|song)/[^/]+/([A-Za-z0-9]+)\?i=([A-Za-z0-9.-]+)'
        r'|https?://music\.apple\.com/(?:[^/]+/)?song/([A-Za-z0-9.-]+)'
    ), 'https://music.apple.com/song/{id}'),
    # SoundCloud
    (Platform.SOUND_CLOUD, re.compile(
        r'https?://(?:www\.)?soundcloud\.com/[\w-]+/[\w-]+'
    ), '{original}'),  # SoundCloud canonical = original URL
    # Bandcamp — subdomain.bandcamp.com/track|album/slug
    (Platform.BANDCAMP, re.compile(
        r'https?://[\w-]+\.bandcamp\.com/(?:track|album)/[\w-]+'
    ), '{original}'),
    # Deezer — track, album, artist, playlist
    (Platform.DEEZER, re.compile(
        r'https?://(?:www\.)?deezer\.com/'
        r'(track|album|artist|playlist)/(\d+)'
    ), 'https://www.deezer.com/{kind}/{id}'),
    # Tidal — track, album, artist, playlist
    (Platform.TIDAL, re.compile(
        r'https?://(?:listen\.)?tidal\.com/'
        r'(track|album|artist|playlist)/(\d+)'
    ), 'https://tidal.com/{kind}/{id}'),
    # Amazon Music
    (Platform.AMAZON_MUSIC, re.compile(
        r'https?://music\.amazon\.(?:com|co\.uk|de|fr|jp|ca|es|it|in|com\.br|com\.mx)/'
        r'(track|album|artist|playlist)/([A-Za-z0-9]+)'
    ), 'https://music.amazon.com/{kind}/{id}'),
    # Qobuz
    (Platform.QOBUZ, re.compile(
        r'https?://(?:play\.)?qobuz\.com/'
        r'(track|album|artist|playlist)/([A-Za-z0-9-]+)'
    ), 'https://www.qobuz.com/{kind}/{id}'),
    # Napster
    (Platform.NAPSTER, re.compile(
        r'https?://(?:us\.)?napster\.com/'
        r'(track|album|artist|playlist)/([A-Za-z0-9-]+)'
    ), 'https://www.napster.com/{kind}/{id}'),
    # Pandora (US only)
    (Platform.PANDORA, re.compile(
        r'https?://(?:www\.)?pandora\.com/'
        r'(track|album|artist|station)/([A-Za-z0-9-]+)'
    ), 'https://www.pandora.com/{kind}/{id}'),
]


def extract_id(match: re.Match[str], group_names: list[str]) -> str | None:
    for g in group_names:
        val = match.group(g)
        if val:
            return val
    return None


def normalize_url(platform: Platform, match: re.Match[str], original: str) -> str:
    """Build a canonical, deduplicated URL form."""
    if platform == Platform.SPOTIFY:
        # match groups: (kind_str, id)
        kind_match = match.group(1)
        id_match = match.group(2)
        if kind_match and id_match:
            return f"https://open.spotify.com/{kind_match}/{id_match}"
    if platform == Platform.YOUTUBE:
        ytid = match.group(1) or match.group(2)
        if ytid:
            return f"https://www.youtube.com/watch?v={ytid}"
    if platform == Platform.YOUTUBE_MUSIC:
        ytid = match.group(1) or match.group(2)
        if ytid:
            return f"https://music.youtube.com/watch?v={ytid}"
    if platform == Platform.DEEZER:
        kind_match = match.group(1)
        id_match = match.group(2)
        if kind_match and id_match:
            return f"https://www.deezer.com/{kind_match}/{id_match}"
    if platform == Platform.TIDAL:
        kind_match = match.group(1)
        id_match = match.group(2)
        if kind_match and id_match:
            return f"https://tidal.com/{kind_match}/{id_match}"
    if platform == Platform.AMAZON_MUSIC:
        kind_match = match.group(1)
        id_match = match.group(2)
        if kind_match and id_match:
            return f"https://music.amazon.com/{kind_match}/{id_match}"
    if platform == Platform.QOBUZ:
        kind_match = match.group(1)
        id_match = match.group(2)
        if kind_match and id_match:
            return f"https://www.qobuz.com/{kind_match}/{id_match}"
    if platform == Platform.NAPSTER:
        kind_match = match.group(1)
        id_match = match.group(2)
        if kind_match and id_match:
            return f"https://www.napster.com/{kind_match}/{id_match}"
    if platform == Platform.PANDORA:
        kind_match = match.group(1)
        id_match = match.group(2)
        if kind_match and id_match:
            return f"https://www.pandora.com/{kind_match}/{id_match}"
    if platform == Platform.APPLE_MUSIC:
        # groups can vary; just return original for simplicity
        return original
    return original


def parse_link(url: str) -> ParsedLink | None:
    """Parse a single URL into a ParsedLink if it matches a known platform."""
    for platform, pattern, _template in _PATTERNS:
        match = pattern.search(url)
        if match:
            canonical = normalize_url(platform, match, url)
            track_id = _extract_track_id(platform, match)
            return ParsedLink(
                platform=platform,
                original_url=url,
                canonical_url=canonical,
                track_id=track_id,
            )
    return None


def _extract_track_id(platform: Platform, match: re.Match[str]) -> str | None:
    if platform == Platform.YOUTUBE:
        return match.group(1) or match.group(2)
    if platform == Platform.YOUTUBE_MUSIC:
        return match.group(1) or match.group(2)
    if platform == Platform.SPOTIFY:
        return match.group(2)
    if platform == Platform.DEEZER:
        return match.group(2)
    if platform == Platform.TIDAL:
        return match.group(2)
    if platform == Platform.AMAZON_MUSIC:
        return match.group(2)
    if platform == Platform.QOBUZ:
        return match.group(2)
    if platform == Platform.NAPSTER:
        return match.group(2)
    if platform == Platform.PANDORA:
        return match.group(2)
    if platform == Platform.APPLE_MUSIC:
        return match.group(2) or match.group(3)
    if platform in (Platform.SOUND_CLOUD, Platform.BANDCAMP):
        return None
    return None


def extract_links(text: str) -> list[ParsedLink]:
    """Extract all music links from a message string."""
    results: list[ParsedLink] = []
    # Find all URLs in the text — Discord embeds may be bare or in angle brackets
    url_pattern = re.compile(
        r'https?://[^\s<>"\']+',
    )
    for match in url_pattern.finditer(text):
        url = match.group(0)
        # strip trailing punctuation that's not part of URL
        url = url.rstrip(".,!?;\"'")
        parsed = parse_link(url)
        if parsed:
            results.append(parsed)
    return results

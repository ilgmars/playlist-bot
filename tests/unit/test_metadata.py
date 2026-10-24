"""Tests for metadata extraction — mocked API calls."""

from unittest.mock import AsyncMock

import httpx
import pytest
import respx

import playlistbot.metadata as meta
from playlistbot.link_parser import ParsedLink, Platform
from playlistbot.metadata import TrackInfo, extract_metadata


@pytest.fixture
def youtube_link():
    return ParsedLink(
        platform=Platform.YOUTUBE,
        original_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        canonical_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        track_id="dQw4w9WgXcQ",
    )


@pytest.fixture
def spotify_link():
    return ParsedLink(
        platform=Platform.SPOTIFY,
        original_url="https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQ3",
        canonical_url="https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQ3",
        track_id="4uLU6hMCjMI75M1A2tKUQ3",
    )


@pytest.fixture
def soundcloud_link():
    return ParsedLink(
        platform=Platform.SOUND_CLOUD,
        original_url="https://soundcloud.com/majorlazertv/keep-it-168",
        canonical_url="https://soundcloud.com/majorlazertv/keep-it-168",
    )


class TestTrackInfo:
    def test_track_info_fields(self):
        t = TrackInfo(
            title="Test Song",
            artist="Test Artist",
            album="Test Album",
            duration_ms=180000,
            source_url="https://example.com",
            source_platform=Platform.YOUTUBE,
        )
        assert t.title == "Test Song"
        assert t.duration_ms == 180000


class TestExtractMetadata:
    @pytest.mark.asyncio
    async def test_extract_youtube_metadata(self, monkeypatch, youtube_link):
        monkeypatch.setattr(
            meta,
            "fetch_youtube_metadata",
            AsyncMock(
                return_value=TrackInfo(
                    title="Never Gonna Give You Up",
                    artist="Rick Astley",
                    source_url=youtube_link.original_url,
                    source_platform=Platform.YOUTUBE,
                )
            ),
        )
        result = await extract_metadata(youtube_link)
        assert isinstance(result, TrackInfo)
        assert result.title == "Never Gonna Give You Up"

    @pytest.mark.asyncio
    @respx.mock
    async def test_extract_spotify_metadata(self, spotify_link):
        respx.get("https://open.spotify.com/oembed").mock(
            return_value=httpx.Response(
                200, json={"title": "Never Gonna Give You Up - Rick Astley"}
            )
        )
        result = await extract_metadata(spotify_link)
        assert isinstance(result, TrackInfo)
        assert result.title == "Never Gonna Give You Up"
        assert result.artist == "Rick Astley"

    @pytest.mark.asyncio
    @respx.mock
    async def test_extract_soundcloud_metadata(self, soundcloud_link):
        respx.get("https://soundcloud.com/oembed").mock(
            return_value=httpx.Response(200, json={"title": "Major Lazer - Keep It 168"})
        )
        result = await extract_metadata(soundcloud_link)
        assert isinstance(result, TrackInfo)
        assert result.title == "Keep It 168"
        assert result.artist == "Major Lazer"

    @pytest.mark.asyncio
    async def test_extract_unknown_platform(self, monkeypatch):
        monkeypatch.setattr(meta, "fetch_youtube_metadata", AsyncMock(return_value=None))
        link = ParsedLink(
            platform=Platform.YOUTUBE,
            original_url="https://example.com",
            canonical_url="https://example.com",
        )
        result = await extract_metadata(link)
        assert result is None


class TestSpotifyOembedParsing:
    @pytest.mark.asyncio
    @respx.mock
    async def test_spotify_oembed_title_format(self):
        """Spotify oEmbed returns 'Song Name - Artist Name'"""
        respx.get("https://open.spotify.com/oembed").mock(
            return_value=httpx.Response(200, json={"title": "Song Name - Artist Name"})
        )
        link = ParsedLink(
            platform=Platform.SPOTIFY,
            original_url="https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQ3",
            canonical_url="https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQ3",
            track_id="4uLU6hMCjMI75M1A2tKUQ3",
        )
        result = await extract_metadata(link)
        assert result is not None
        assert result.title == "Song Name"
        assert result.artist == "Artist Name"

"""Network-mocked tests for every metadata fetcher and the dispatch layer."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

import playlistbot.metadata as meta
from playlistbot.link_parser import ParsedLink, Platform
from playlistbot.metadata import (
    TrackInfo,
    extract_metadata,
    fetch_amazon_music_metadata,
    fetch_apple_music_metadata,
    fetch_bandcamp_metadata,
    fetch_deezer_metadata,
    fetch_deezer_oembed,
    fetch_napster_metadata,
    fetch_pandora_metadata,
    fetch_qobuz_metadata,
    fetch_soundcloud_metadata,
    fetch_spotify_metadata,
    fetch_tidal_metadata,
    fetch_youtube_metadata,
)

TRACK_ID = "4uLU6hMCjMI75M1A2tKUQ3"
SPOTIFY_URL = f"https://open.spotify.com/track/{TRACK_ID}"


@pytest.fixture
def youtube_link():
    return ParsedLink(
        platform=Platform.YOUTUBE,
        original_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        canonical_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        track_id="dQw4w9WgXcQ",
    )


def _fake_ytdlp(info=None, error=None):
    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            if error:
                raise error
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            return info

    return FakeYDL


class TestYoutubeFetcher:
    @pytest.mark.asyncio
    async def test_full_metadata(self, monkeypatch):
        monkeypatch.setattr(
            meta.yt_dlp,
            "YoutubeDL",
            _fake_ytdlp(
                {
                    "title": "Song",
                    "artist": "Artist",
                    "album": "Album",
                    "duration": 213,
                }
            ),
        )
        result = await fetch_youtube_metadata("https://youtu.be/x")
        assert result.title == "Song"
        assert result.artist == "Artist"
        assert result.album == "Album"
        assert result.duration_ms == 213000
        assert result.source_platform == Platform.YOUTUBE

    @pytest.mark.asyncio
    async def test_falls_back_to_uploader_and_unknowns(self, monkeypatch):
        monkeypatch.setattr(
            meta.yt_dlp,
            "YoutubeDL",
            _fake_ytdlp({"uploader": "Uploader", "duration": 0}),
        )
        result = await fetch_youtube_metadata("https://youtu.be/x")
        assert result.title == "Unknown"
        assert result.artist == "Uploader"
        assert result.duration_ms is None

    @pytest.mark.asyncio
    async def test_no_uploader(self, monkeypatch):
        monkeypatch.setattr(meta.yt_dlp, "YoutubeDL", _fake_ytdlp({}))
        result = await fetch_youtube_metadata("https://youtu.be/x")
        assert result.artist == "Unknown"

    @pytest.mark.asyncio
    async def test_returns_none_on_ytdlp_error(self, monkeypatch):
        monkeypatch.setattr(meta.yt_dlp, "YoutubeDL", _fake_ytdlp(error=RuntimeError("boom")))
        assert await fetch_youtube_metadata("https://youtu.be/x") is None


class TestSpotifyFetcher:
    @pytest.mark.asyncio
    async def test_invalid_url_returns_none(self):
        assert await fetch_spotify_metadata("https://open.spotify.com/album/x") is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_token_path_returns_rich_data(self):
        respx.get(f"https://api.spotify.com/v1/tracks/{TRACK_ID}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "name": "Song",
                    "artists": [{"name": "A"}, {"name": "B"}],
                    "album": {"name": "Album"},
                    "duration_ms": 1234,
                    "external_ids": {"isrc": "ISRC123"},
                },
            )
        )
        result = await fetch_spotify_metadata(SPOTIFY_URL, token="tok")
        assert result.title == "Song"
        assert result.artist == "A, B"
        assert result.album == "Album"
        assert result.isrc == "ISRC123"
        assert result.duration_ms == 1234
        assert result.source_platform == Platform.SPOTIFY

    @pytest.mark.asyncio
    @respx.mock
    async def test_token_path_without_artists(self):
        respx.get(f"https://api.spotify.com/v1/tracks/{TRACK_ID}").mock(
            return_value=httpx.Response(200, json={"name": "Song"})
        )
        result = await fetch_spotify_metadata(SPOTIFY_URL, token="tok")
        assert result.artist == "Unknown"
        assert result.album is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_token_failure_falls_back_to_oembed(self):
        respx.get(f"https://api.spotify.com/v1/tracks/{TRACK_ID}").mock(
            return_value=httpx.Response(401)
        )
        respx.get("https://open.spotify.com/oembed").mock(
            return_value=httpx.Response(200, json={"title": "Song - Artist"})
        )
        result = await fetch_spotify_metadata(SPOTIFY_URL, token="bad")
        assert result.title == "Song"
        assert result.artist == "Artist"

    @pytest.mark.asyncio
    @respx.mock
    async def test_oembed_without_separator(self):
        respx.get("https://open.spotify.com/oembed").mock(
            return_value=httpx.Response(200, json={"title": "Song"})
        )
        result = await fetch_spotify_metadata(SPOTIFY_URL)
        assert result.title == "Unknown (Spotify)"

    @pytest.mark.asyncio
    @respx.mock
    async def test_oembed_non_200(self):
        respx.get("https://open.spotify.com/oembed").mock(return_value=httpx.Response(500))
        result = await fetch_spotify_metadata(SPOTIFY_URL)
        assert result.title == "Unknown (Spotify)"

    @pytest.mark.asyncio
    @respx.mock
    async def test_oembed_network_error(self):
        respx.get("https://open.spotify.com/oembed").mock(side_effect=httpx.ConnectError("nope"))
        result = await fetch_spotify_metadata(SPOTIFY_URL)
        assert result.title == "Unknown (Spotify)"


class TestAppleFetcher:
    URL = "https://music.apple.com/us/song/x/1?i=2"

    @pytest.mark.asyncio
    @respx.mock
    async def test_script_json(self):
        html = '<script>{"name":"Song","artistName":"Artist"}</script>'
        respx.get(self.URL).mock(return_value=httpx.Response(200, text=html))
        result = await fetch_apple_music_metadata(self.URL)
        assert (result.title, result.artist) == ("Song", "Artist")

    @pytest.mark.asyncio
    @respx.mock
    async def test_title_fallback(self):
        html = "<html><title>Song — Artist | Apple Music</title></html>"
        respx.get(self.URL).mock(return_value=httpx.Response(200, text=html))
        result = await fetch_apple_music_metadata(self.URL)
        assert (result.title, result.artist) == ("Song", "Artist")

    @pytest.mark.asyncio
    @respx.mock
    async def test_unparseable_page(self):
        respx.get(self.URL).mock(return_value=httpx.Response(200, text="<html></html>"))
        assert await fetch_apple_music_metadata(self.URL) is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_non_200(self):
        respx.get(self.URL).mock(return_value=httpx.Response(404))
        assert await fetch_apple_music_metadata(self.URL) is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_network_error(self):
        respx.get(self.URL).mock(side_effect=httpx.ConnectError("nope"))
        assert await fetch_apple_music_metadata(self.URL) is None


class TestSoundcloudFetcher:
    URL = "https://soundcloud.com/a/b"

    @pytest.mark.asyncio
    @respx.mock
    async def test_success(self):
        respx.get("https://soundcloud.com/oembed").mock(
            return_value=httpx.Response(200, json={"title": "Artist - Song"})
        )
        result = await fetch_soundcloud_metadata(self.URL)
        assert (result.title, result.artist) == ("Song", "Artist")
        assert result.source_platform == Platform.SOUND_CLOUD

    @pytest.mark.asyncio
    @respx.mock
    async def test_title_without_separator(self):
        respx.get("https://soundcloud.com/oembed").mock(
            return_value=httpx.Response(200, json={"title": "Song"})
        )
        result = await fetch_soundcloud_metadata(self.URL)
        assert (result.title, result.artist) == ("Song", "Unknown")

    @pytest.mark.asyncio
    @respx.mock
    async def test_non_200(self):
        respx.get("https://soundcloud.com/oembed").mock(return_value=httpx.Response(500))
        result = await fetch_soundcloud_metadata(self.URL)
        assert result.title == "Unknown (SoundCloud)"

    @pytest.mark.asyncio
    @respx.mock
    async def test_network_error(self):
        respx.get("https://soundcloud.com/oembed").mock(side_effect=httpx.ConnectError("x"))
        result = await fetch_soundcloud_metadata(self.URL)
        assert result.title == "Unknown (SoundCloud)"


class TestBandcampFetcher:
    URL = "https://artist.bandcamp.com/track/song"

    @pytest.mark.asyncio
    @respx.mock
    async def test_success(self):
        payload = [{"title": "Song", "artist": "Artist", "album_title": "LP", "duration": 3}]
        html = f"trackinfo : {json.dumps(payload)}"
        respx.get(self.URL).mock(return_value=httpx.Response(200, text=html))
        result = await fetch_bandcamp_metadata(self.URL)
        assert result.title == "Song"
        assert result.artist == "Artist"
        assert result.album == "LP"
        assert result.duration_ms == 3000
        assert result.source_platform == Platform.BANDCAMP

    @pytest.mark.asyncio
    @respx.mock
    async def test_invalid_json(self):
        respx.get(self.URL).mock(return_value=httpx.Response(200, text="trackinfo : [{bad}]"))
        assert await fetch_bandcamp_metadata(self.URL) is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_list(self):
        respx.get(self.URL).mock(return_value=httpx.Response(200, text="trackinfo : []"))
        assert await fetch_bandcamp_metadata(self.URL) is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_missing_block(self):
        respx.get(self.URL).mock(return_value=httpx.Response(200, text="<html></html>"))
        assert await fetch_bandcamp_metadata(self.URL) is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_non_200(self):
        respx.get(self.URL).mock(return_value=httpx.Response(403))
        assert await fetch_bandcamp_metadata(self.URL) is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_network_error(self):
        respx.get(self.URL).mock(side_effect=httpx.ConnectError("x"))
        assert await fetch_bandcamp_metadata(self.URL) is None


class TestDeezerFetcher:
    URL = "https://www.deezer.com/track/123"

    @pytest.mark.asyncio
    async def test_non_track_url(self):
        assert await fetch_deezer_metadata("https://www.deezer.com/album/1") is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_success(self):
        respx.get("https://api.deezer.com/track/123").mock(
            return_value=httpx.Response(
                200,
                json={
                    "title": "Song",
                    "artist": {"name": "Artist"},
                    "album": {"title": "LP"},
                    "duration": 2,
                    "isrc": "I1",
                },
            )
        )
        result = await fetch_deezer_metadata(self.URL)
        assert result.title == "Song"
        assert result.artist == "Artist"
        assert result.album == "LP"
        assert result.duration_ms == 2000
        assert result.isrc == "I1"

    @pytest.mark.asyncio
    @respx.mock
    async def test_non_200(self):
        respx.get("https://api.deezer.com/track/123").mock(return_value=httpx.Response(500))
        result = await fetch_deezer_metadata(self.URL)
        assert result.title == "Unknown (Deezer)"

    @pytest.mark.asyncio
    @respx.mock
    async def test_network_error(self):
        respx.get("https://api.deezer.com/track/123").mock(side_effect=httpx.ConnectError("x"))
        result = await fetch_deezer_metadata(self.URL)
        assert result.title == "Unknown (Deezer)"

    @pytest.mark.asyncio
    @respx.mock
    async def test_deezer_oembed_success(self):
        respx.get("https://api.deezer.com/oembed").mock(
            return_value=httpx.Response(200, json={"title": "Song", "author_name": "Artist"})
        )
        result = await fetch_deezer_oembed("https://www.deezer.com/album/987")
        assert result is not None
        assert result.title == "Song"
        assert result.artist == "Artist"
        assert result.source_platform == Platform.DEEZER

    @pytest.mark.asyncio
    @respx.mock
    async def test_deezer_oembed_failures(self):
        respx.get("https://api.deezer.com/oembed").mock(return_value=httpx.Response(404))
        assert await fetch_deezer_oembed("https://www.deezer.com/album/987") is None
        respx.get("https://api.deezer.com/oembed").mock(side_effect=httpx.ConnectError("x"))
        assert await fetch_deezer_oembed("https://www.deezer.com/album/987") is None


class TestRateLimitBackoff:
    def test_parse_retry_after(self):
        assert meta._parse_retry_after("2.5", 0, 0.5) == 2.5
        assert meta._parse_retry_after("invalid", 1, 0.5) == 1.0
        assert meta._parse_retry_after(None, 2, 0.5) == 2.0

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_get_retries_on_429_with_retry_after(self, monkeypatch):
        sleep_mock = AsyncMock()
        monkeypatch.setattr(meta.asyncio, "sleep", sleep_mock)
        route = respx.get("https://api.deezer.com/track/999").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "1"}),
                httpx.Response(200, json={"title": "Retried Song", "artist": {"name": "Artist"}}),
            ]
        )
        result = await fetch_deezer_metadata("https://www.deezer.com/track/999")
        assert result is not None
        assert result.title == "Retried Song"
        assert route.call_count == 2
        sleep_mock.assert_awaited_once_with(1.0)


class TestTidalFetcher:
    URL = "https://tidal.com/track/1"

    @pytest.mark.asyncio
    @respx.mock
    async def test_title_parse(self):
        respx.get(self.URL).mock(
            return_value=httpx.Response(200, text="<title>Song - Artist</title>")
        )
        result = await fetch_tidal_metadata(self.URL)
        assert (result.title, result.artist) == ("Song", "Artist")
        assert result.source_platform == Platform.TIDAL

    @pytest.mark.asyncio
    @respx.mock
    async def test_title_without_separator(self):
        respx.get(self.URL).mock(return_value=httpx.Response(200, text="<title>Song</title>"))
        result = await fetch_tidal_metadata(self.URL)
        assert result.title == "Unknown (Tidal)"

    @pytest.mark.asyncio
    @respx.mock
    async def test_non_200(self):
        respx.get(self.URL).mock(return_value=httpx.Response(404))
        assert await fetch_tidal_metadata(self.URL) is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_network_error(self):
        respx.get(self.URL).mock(side_effect=httpx.ConnectError("x"))
        assert await fetch_tidal_metadata(self.URL) is None


class TestStaticFallbacks:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("fetcher", "title", "platform"),
        [
            (fetch_amazon_music_metadata, "Unknown (Amazon Music)", Platform.AMAZON_MUSIC),
            (fetch_qobuz_metadata, "Unknown (Qobuz)", Platform.QOBUZ),
            (fetch_napster_metadata, "Unknown (Napster)", Platform.NAPSTER),
            (fetch_pandora_metadata, "Unknown (Pandora)", Platform.PANDORA),
        ],
    )
    async def test_fallbacks(self, fetcher, title, platform):
        result = await fetcher("https://example.com/x")
        assert result.title == title
        assert result.artist == "Unknown"
        assert result.source_platform == platform


class TestExtractMetadataDispatch:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("platform", "fetcher_name"),
        [
            (Platform.YOUTUBE, "fetch_youtube_metadata"),
            (Platform.YOUTUBE_MUSIC, "fetch_youtube_metadata"),
            (Platform.APPLE_MUSIC, "fetch_apple_music_metadata"),
            (Platform.SOUND_CLOUD, "fetch_soundcloud_metadata"),
            (Platform.BANDCAMP, "fetch_bandcamp_metadata"),
            (Platform.DEEZER, "fetch_deezer_metadata"),
            (Platform.TIDAL, "fetch_tidal_metadata"),
            (Platform.AMAZON_MUSIC, "fetch_amazon_music_metadata"),
            (Platform.QOBUZ, "fetch_qobuz_metadata"),
            (Platform.NAPSTER, "fetch_napster_metadata"),
            (Platform.PANDORA, "fetch_pandora_metadata"),
        ],
    )
    async def test_dispatch(self, monkeypatch, platform, fetcher_name):
        fake = AsyncMock(return_value=TrackInfo(title="T", artist="A"))
        monkeypatch.setattr(meta, fetcher_name, fake)
        link = ParsedLink(
            platform=platform,
            original_url="https://example.com/original",
            canonical_url="https://example.com/original",
        )
        result = await extract_metadata(link)
        assert result.title == "T"
        fake.assert_awaited_once_with("https://example.com/original")

    @pytest.mark.asyncio
    async def test_spotify_uses_env_token(self, monkeypatch):
        fake = AsyncMock(return_value=TrackInfo(title="T", artist="A"))
        monkeypatch.setattr(meta, "fetch_spotify_metadata", fake)
        monkeypatch.setenv("SPOTIFY_ACCESS_TOKEN", "env-token")
        link = ParsedLink(
            platform=Platform.SPOTIFY,
            original_url=SPOTIFY_URL,
            canonical_url=SPOTIFY_URL,
        )
        await extract_metadata(link)
        fake.assert_awaited_once_with(SPOTIFY_URL, "env-token")

    @pytest.mark.asyncio
    async def test_unmapped_platform_returns_none(self, monkeypatch):
        """A platform the dispatch table does not know falls through to None."""
        link = ParsedLink(
            platform=Platform.YOUTUBE,
            original_url="https://example.com",
            canonical_url="https://example.com",
        )
        # Simulate an unmapped enum member without touching the real Platform.
        object.__setattr__(link, "platform", "something_else")
        assert await extract_metadata(link) is None

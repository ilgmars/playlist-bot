"""Tests for the playlist manager — mocked API calls."""

import pytest

from playlistbot.link_parser import ParsedLink, Platform
from playlistbot.playlist import PlaylistManager


@pytest.fixture
def manager():
    return PlaylistManager()


class TestSpotifyTrackIdExtraction:
    def test_extract_spotify_track_id_valid(self, manager):
        url = "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQ3"
        assert manager._extract_spotify_track_id(url) == "4uLU6hMCjMI75M1A2tKUQ3"

    def test_extract_spotify_track_id_none(self, manager):
        url = "https://open.spotify.com/artist/4uLU6hMCjMI75M1A2tKUQ3"
        assert manager._extract_spotify_track_id(url) is None

    def test_extract_spotify_track_id_no_match(self, manager):
        url = "https://google.com"
        assert manager._extract_spotify_track_id(url) is None


class TestAddtoPlaylistDispatch:
    def test_add_to_playlist_returns_false_without_creds(self, manager):
        """Without configured credentials, all platform writes return False."""
        link = ParsedLink(
            platform=Platform.YOUTUBE,
            original_url="https://youtu.be/dQw4w9WgXcQ",
            canonical_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            track_id="dQw4w9WgXcQ",
        )
        result = manager.add_to_playlist(
            platform=Platform.YOUTUBE,
            channel_id="123",
            playlist_id="PL123",
            link=link,
        )
        assert result is False

    def test_add_to_playlist_spotify_returns_false_without_creds(self, manager):
        link = ParsedLink(
            platform=Platform.SPOTIFY,
            original_url="https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQ3",
            canonical_url="https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQ3",
            track_id="4uLU6hMCjMI75M1A2tKUQ3",
        )
        result = manager.add_to_playlist(
            platform=Platform.SPOTIFY,
            channel_id="123",
            playlist_id="PL123",
            link=link,
        )
        assert result is False

    def test_add_to_playlist_unsupported_platform(self, manager):
        """Apple Music and SoundCloud fall through to False."""
        link = ParsedLink(
            platform=Platform.APPLE_MUSIC,
            original_url="https://music.apple.com/us/song/test/123",
            canonical_url="https://music.apple.com/us/song/test/123",
        )
        result = manager.add_to_playlist(
            platform=Platform.APPLE_MUSIC,
            channel_id="123",
            playlist_id="PL123",
            link=link,
        )
        assert result is False

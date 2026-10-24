"""Unit tests for playlist.PlaylistManager."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from playlistbot import playlist as pl
from playlistbot.link_parser import ParsedLink, Platform

TRACK_ID = "4uLU6hMCjMI75M1A2tKUQ3"


def _link(platform: Platform, url: str, track_id: str | None = None) -> ParsedLink:
    return ParsedLink(
        platform=platform,
        original_url=url,
        canonical_url=url,
        track_id=track_id,
    )


def _youtube_client(playlist_id: str = "PL123") -> MagicMock:
    client = MagicMock()
    client.playlists.return_value.insert.return_value.execute.return_value = {"id": playlist_id}
    return client


class TestEnsureYoutube:
    def test_without_client_secrets_returns_none(self, monkeypatch):
        monkeypatch.delenv("YOUTUBE_CLIENT_SECRETS", raising=False)
        mgr = pl.PlaylistManager()
        assert mgr._ensure_youtube() is None

    def test_flow_failure_returns_none(self, monkeypatch):
        monkeypatch.setenv("YOUTUBE_CLIENT_SECRETS", "client.json")
        monkeypatch.setattr(
            pl,
            "Flow",
            SimpleNamespace(from_client_secrets_file=MagicMock(side_effect=RuntimeError("bad"))),
        )
        assert pl.PlaylistManager()._ensure_youtube() is None

    def test_missing_token_returns_none(self, monkeypatch):
        monkeypatch.setenv("YOUTUBE_CLIENT_SECRETS", "client.json")
        monkeypatch.setattr(pl, "Flow", SimpleNamespace(from_client_secrets_file=MagicMock()))
        monkeypatch.setattr(pl.os.path, "exists", lambda _p: False)
        assert pl.PlaylistManager()._ensure_youtube() is None

    def test_valid_cached_credentials_build_client(self, monkeypatch):
        mgr = pl.PlaylistManager()
        creds = SimpleNamespace(valid=True, expired=False, refresh_token=None)
        mgr._youtube_creds = creds
        build = MagicMock(return_value="client")
        monkeypatch.setattr(mgr, "_build_youtube_client", build)

        assert mgr._ensure_youtube() == "client"
        build.assert_called_once_with()

    def test_expired_credentials_with_refresh_token_refresh(self, monkeypatch):
        monkeypatch.setenv("YOUTUBE_CLIENT_SECRETS", "client.json")
        monkeypatch.setattr(pl, "Flow", SimpleNamespace(from_client_secrets_file=MagicMock()))

        creds = MagicMock()
        creds.valid = False
        creds.expired = True
        creds.refresh_token = "refresh-token"

        def _refresh(_client):
            creds.valid = True

        creds.refresh.side_effect = _refresh
        monkeypatch.setattr(
            pl,
            "Credentials",
            SimpleNamespace(from_authorized_user_file=MagicMock(return_value=creds)),
        )
        monkeypatch.setattr(pl.os.path, "exists", lambda _p: True)

        mgr = pl.PlaylistManager()
        build = MagicMock(return_value="client")
        monkeypatch.setattr(mgr, "_build_youtube_client", build)

        assert mgr._ensure_youtube() == "client"
        creds.refresh.assert_called_once()
        build.assert_called_once_with()

    def test_expired_without_refresh_token_returns_none(self, monkeypatch):
        monkeypatch.setenv("YOUTUBE_CLIENT_SECRETS", "client.json")
        monkeypatch.setattr(pl, "Flow", SimpleNamespace(from_client_secrets_file=MagicMock()))

        creds = SimpleNamespace(valid=False, expired=True, refresh_token=None)
        monkeypatch.setattr(
            pl,
            "Credentials",
            SimpleNamespace(from_authorized_user_file=MagicMock(return_value=creds)),
        )
        monkeypatch.setattr(pl.os.path, "exists", lambda _p: True)

        assert pl.PlaylistManager()._ensure_youtube() is None


class TestYoutubePlaylistOps:
    def test_create_playlist_without_client_returns_none(self, monkeypatch):
        mgr = pl.PlaylistManager()
        monkeypatch.setattr(mgr, "_ensure_youtube", MagicMock(return_value=None))
        assert mgr._get_or_create_youtube_playlist("chan") is None

    def test_create_playlist_returns_id(self, monkeypatch):
        mgr = pl.PlaylistManager()
        client = _youtube_client("PL999")
        monkeypatch.setattr(mgr, "_ensure_youtube", MagicMock(return_value=client))

        assert mgr._get_or_create_youtube_playlist("chan") == "PL999"
        body = client.playlists.return_value.insert.call_args.kwargs["body"]
        assert body["status"]["privacyStatus"] == "private"

    def test_add_without_client_returns_false(self, monkeypatch):
        mgr = pl.PlaylistManager()
        monkeypatch.setattr(mgr, "_ensure_youtube", MagicMock(return_value=None))
        assert mgr.add_to_youtube_playlist("chan", "PL1", "vid") is False

    def test_add_success_inserts_at_position_zero(self, monkeypatch):
        mgr = pl.PlaylistManager()
        client = MagicMock()
        monkeypatch.setattr(mgr, "_ensure_youtube", MagicMock(return_value=client))

        assert mgr.add_to_youtube_playlist("chan", "PL1", "vid") is True
        body = client.playlistItems.return_value.insert.call_args.kwargs["body"]
        assert body["snippet"]["position"] == 0
        assert body["snippet"]["resourceId"]["videoId"] == "vid"

    def test_add_api_error_returns_false(self, monkeypatch):
        mgr = pl.PlaylistManager()
        client = MagicMock()
        client.playlistItems.return_value.insert.return_value.execute.side_effect = RuntimeError(
            "boom"
        )
        monkeypatch.setattr(mgr, "_ensure_youtube", MagicMock(return_value=client))

        assert mgr.add_to_youtube_playlist("chan", "PL1", "vid") is False


class TestEnsureSpotify:
    def test_cached_client_returned(self):
        mgr = pl.PlaylistManager()
        mgr._spotify = "client"
        assert mgr._ensure_spotify() == "client"

    def test_missing_credentials_returns_none(self, monkeypatch):
        monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
        monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
        assert pl.PlaylistManager()._ensure_spotify() is None

    def test_missing_token_returns_none_and_logs_auth_url(self, monkeypatch):
        monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id")
        monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")

        oauth = MagicMock()
        oauth.get_cached_token.return_value = None
        oauth.get_authorize_url.return_value = "https://accounts.spotify.com/authorize"
        monkeypatch.setattr(pl, "SpotifyOAuth", MagicMock(return_value=oauth))

        assert pl.PlaylistManager()._ensure_spotify() is None
        oauth.get_authorize_url.assert_called_once_with()

    def test_expired_token_is_refreshed(self, monkeypatch):
        monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id")
        monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")

        oauth = MagicMock()
        oauth.get_cached_token.return_value = {"refresh_token": "rt"}
        oauth.is_token_expired.return_value = True
        oauth.refresh_access_token.return_value = {"access_token": "new"}
        monkeypatch.setattr(pl, "SpotifyOAuth", MagicMock(return_value=oauth))
        spotify_client = MagicMock()
        monkeypatch.setattr(pl, "Spotify", MagicMock(return_value=spotify_client))

        assert pl.PlaylistManager()._ensure_spotify() is spotify_client
        oauth.refresh_access_token.assert_called_once_with("rt")
        pl.Spotify.assert_called_once_with(auth_manager=oauth)

    def test_valid_token_builds_client(self, monkeypatch):
        monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id")
        monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")

        oauth = MagicMock()
        oauth.get_cached_token.return_value = {"access_token": "ok"}
        oauth.is_token_expired.return_value = False
        monkeypatch.setattr(pl, "SpotifyOAuth", MagicMock(return_value=oauth))
        spotify_client = MagicMock()
        monkeypatch.setattr(pl, "Spotify", MagicMock(return_value=spotify_client))

        assert pl.PlaylistManager()._ensure_spotify() is spotify_client
        oauth.refresh_access_token.assert_not_called()


class TestSpotifyPlaylistOps:
    def test_extract_track_id_valid(self):
        mgr = pl.PlaylistManager()
        assert (
            mgr._extract_spotify_track_id(f"https://open.spotify.com/track/{TRACK_ID}") == TRACK_ID
        )

    def test_extract_track_id_invalid_returns_none(self):
        assert pl.PlaylistManager()._extract_spotify_track_id("https://example.com/song") is None

    def test_add_without_client_returns_false(self, monkeypatch):
        mgr = pl.PlaylistManager()
        monkeypatch.setattr(mgr, "_ensure_spotify", MagicMock(return_value=None))
        url = f"https://open.spotify.com/track/{TRACK_ID}"
        assert mgr.add_to_spotify_playlist("chan", "pl", url) is False

    def test_add_invalid_url_returns_false(self, monkeypatch):
        mgr = pl.PlaylistManager()
        monkeypatch.setattr(mgr, "_ensure_spotify", MagicMock(return_value=MagicMock()))
        assert mgr.add_to_spotify_playlist("chan", "pl", "https://example.com/song") is False

    def test_add_success_prepends_track(self, monkeypatch):
        mgr = pl.PlaylistManager()
        client = MagicMock()
        monkeypatch.setattr(mgr, "_ensure_spotify", MagicMock(return_value=client))

        url = f"https://open.spotify.com/track/{TRACK_ID}"
        assert mgr.add_to_spotify_playlist("chan", "pl", url) is True
        client.playlist_add_items.assert_called_once_with(
            "pl", [f"spotify:track:{TRACK_ID}"], position=0
        )

    def test_add_api_error_returns_false(self, monkeypatch):
        mgr = pl.PlaylistManager()
        client = MagicMock()
        client.playlist_add_items.side_effect = RuntimeError("boom")
        monkeypatch.setattr(mgr, "_ensure_spotify", MagicMock(return_value=client))

        url = f"https://open.spotify.com/track/{TRACK_ID}"
        assert mgr.add_to_spotify_playlist("chan", "pl", url) is False

    def test_create_playlist_without_client_returns_none(self, monkeypatch):
        mgr = pl.PlaylistManager()
        monkeypatch.setattr(mgr, "_ensure_spotify", MagicMock(return_value=None))
        assert mgr.create_spotify_playlist("chan") is None

    def test_create_playlist_returns_id(self, monkeypatch):
        mgr = pl.PlaylistManager()
        client = MagicMock()
        client.current_user.return_value = {"id": "user1"}
        client.user_playlist_create.return_value = {"id": "pl1"}
        monkeypatch.setattr(mgr, "_ensure_spotify", MagicMock(return_value=client))

        assert mgr.create_spotify_playlist("chan") == "pl1"
        kwargs = client.user_playlist_create.call_args.kwargs
        assert kwargs["user"] == "user1"
        assert kwargs["public"] is False


class TestDispatch:
    @pytest.mark.parametrize(
        ("platform", "url", "track_id"),
        [
            (Platform.YOUTUBE, "https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            (Platform.YOUTUBE_MUSIC, "https://music.youtube.com/watch?v=dQw4w9WgXcQ", None),
        ],
    )
    def test_youtube_family_dispatches_to_youtube(self, monkeypatch, platform, url, track_id):
        mgr = pl.PlaylistManager()
        add = MagicMock(return_value=True)
        monkeypatch.setattr(mgr, "add_to_youtube_playlist", add)
        link = _link(platform, url, track_id)

        assert mgr.add_to_playlist(platform, "chan", "PL1", link) is True
        add.assert_called_once()
        assert add.call_args.args[2] == "dQw4w9WgXcQ"

    def test_youtube_falls_back_to_v_query_param(self, monkeypatch):
        mgr = pl.PlaylistManager()
        add = MagicMock(return_value=True)
        monkeypatch.setattr(mgr, "add_to_youtube_playlist", add)
        link = _link(Platform.YOUTUBE, "https://www.youtube.com/watch?v=dQw4w9WgXcQ")

        mgr.add_to_playlist(Platform.YOUTUBE, "chan", "PL1", link)
        assert add.call_args.args[2] == "dQw4w9WgXcQ"

    def test_spotify_dispatches_to_spotify(self, monkeypatch):
        mgr = pl.PlaylistManager()
        add = MagicMock(return_value=True)
        monkeypatch.setattr(mgr, "add_to_spotify_playlist", add)
        url = f"https://open.spotify.com/track/{TRACK_ID}"

        assert mgr.add_to_playlist(Platform.SPOTIFY, "chan", "pl", _link(Platform.SPOTIFY, url))
        add.assert_called_once_with("chan", "pl", url)

    def test_unwritable_platform_returns_false(self):
        mgr = pl.PlaylistManager()
        url = "https://music.apple.com/us/song/x/1"
        link = _link(Platform.APPLE_MUSIC, url)
        assert mgr.add_to_playlist(Platform.APPLE_MUSIC, "chan", "pl", link) is False

    def test_mismatched_platform_returns_false(self, monkeypatch):
        mgr = pl.PlaylistManager()
        yt_add = MagicMock()
        sp_add = MagicMock()
        monkeypatch.setattr(mgr, "add_to_youtube_playlist", yt_add)
        monkeypatch.setattr(mgr, "add_to_spotify_playlist", sp_add)

        sp_link = _link(Platform.SPOTIFY, f"https://open.spotify.com/track/{TRACK_ID}", TRACK_ID)
        yt_link = _link(Platform.YOUTUBE, "https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ")

        assert mgr.add_to_playlist(Platform.YOUTUBE, "chan", "PL1", sp_link) is False
        assert mgr.add_to_playlist(Platform.SPOTIFY, "chan", "pl", yt_link) is False
        yt_add.assert_not_called()
        sp_add.assert_not_called()


class TestM3uExport:
    def test_generate_m3u_and_export_file(self, tmp_path):
        songs = [
            {
                "title": "Evermore",
                "artist": "Taylor Swift",
                "duration": 244000,
                "links": [
                    {
                        "platform": "youtube",
                        "original_url": "https://www.youtube.com/watch?v=abc12345678",
                    },
                    {
                        "platform": "apple_music",
                        "original_url": "https://music.apple.com/us/song/evermore/1534087881",
                    },
                ],
            },
            {
                "title": "Short Track",
                "artist": "Artist B",
                "duration": 120,
                "links": [
                    {
                        "platform": "apple_music",
                        "original_url": "https://music.apple.com/us/song/short/2",
                    }
                ],
            },
            {"title": "No Links", "artist": "Skip Me", "links": []},
        ]
        mgr = pl.PlaylistManager()
        out_file = tmp_path / "export" / "apple.m3u"
        m3u = mgr.export_m3u(
            songs,
            output_path=str(out_file),
            title="Apple Export",
            preferred_platform="apple_music",
        )
        assert m3u.startswith("#EXTM3U\n#PLAYLIST:Apple Export\n")
        assert "#EXTINF:244,Taylor Swift - Evermore" in m3u
        assert "https://music.apple.com/us/song/evermore/1534087881" in m3u
        assert "#EXTINF:120,Artist B - Short Track" in m3u
        assert "Skip Me" not in m3u
        assert out_file.read_text(encoding="utf-8") == m3u


class TestBackoffHelper:
    def test_retries_on_429_and_respects_retry_after(self, monkeypatch):
        sleep_mock = MagicMock()
        monkeypatch.setattr(pl.time, "sleep", sleep_mock)

        class RateLimitError(Exception):
            http_status = 429
            headers = {"Retry-After": "1.5"}

        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RateLimitError("429 Too Many Requests")
            return "ok"

        assert pl._call_with_backoff(flaky, max_retries=3, base_delay=0.5) == "ok"
        assert calls["n"] == 2
        sleep_mock.assert_called_once_with(1.5)

    def test_retries_with_exponential_backoff_when_no_header(self, monkeypatch):
        sleep_mock = MagicMock()
        monkeypatch.setattr(pl.time, "sleep", sleep_mock)

        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("HTTP 429 rate limit")
            return "done"

        assert pl._call_with_backoff(flaky, max_retries=3, base_delay=0.25) == "done"
        assert calls["n"] == 3
        assert sleep_mock.call_count == 2

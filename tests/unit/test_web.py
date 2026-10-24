"""Tests for the web API endpoints."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from playlistbot.db import insert_song
from playlistbot.models import Platform, SongLink
from playlistbot.web import app


@pytest.fixture(autouse=True)
async def setup_db(tmp_path, monkeypatch):
    """Initialize an in-memory SQLite DB for each test."""
    import os

    import playlistbot.db as dbmod

    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(dbmod, "DB_PATH", db_path)
    monkeypatch.setattr(dbmod, "DB_PATH", db_path)
    os.environ["DB_PATH"] = db_path
    # Also patch the module-level constant in any imported references
    await dbmod.init_db()
    yield
    if await asyncio.to_thread(os.path.exists, db_path):
        await asyncio.to_thread(os.remove, db_path)


@pytest.fixture
def client():
    return TestClient(app)


class TestHealthz:
    def test_healthz_returns_ok(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestIndexPage:
    def test_index_returns_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Playlist Bot Dashboard" in resp.text
        assert "Playlist Bot" in resp.text


class TestSongsApi:
    def test_songs_api_empty(self, client):
        resp = client.get("/api/songs")
        assert resp.status_code == 200
        assert "No songs" in resp.text

    @pytest.mark.asyncio
    async def test_songs_api_with_data(self, client):
        links = [
            SongLink(
                platform=Platform.YOUTUBE,
                original_url="https://youtube.com/watch?v=abc",
                canonical_url="https://youtube.com/watch?v=abc",
                track_id="abc123abc123",
            )
        ]
        await insert_song(
            title="Test Song",
            artist="Test Artist",
            links=links,
            channel_id="chan-1",
        )
        resp = client.get("/api/songs")
        assert resp.status_code == 200
        assert "Test Song" in resp.text
        assert "Test Artist" in resp.text
        assert "youtube" in resp.text.lower()
        assert 'class="platform-badge youtube"' in resp.text
        assert 'data-platform="youtube"' in resp.text
        assert 'data-channel="chan-1"' in resp.text

    @pytest.mark.asyncio
    async def test_songs_api_escapes_html_and_filters_by_channel(self, client):
        await insert_song(
            title="<script>alert(1)</script>",
            artist="Artist & Co",
            links=[
                SongLink(
                    platform=Platform.SPOTIFY,
                    original_url="https://open.spotify.com/track/123",
                    canonical_url="https://open.spotify.com/track/123",
                    track_id="123",
                )
            ],
            channel_id="chan-a",
        )
        await insert_song(
            title="Channel B Song",
            artist="B Artist",
            links=[
                SongLink(
                    platform=Platform.DEEZER,
                    original_url="https://www.deezer.com/track/456",
                    canonical_url="https://www.deezer.com/track/456",
                    track_id="456",
                )
            ],
            channel_id="chan-b",
        )
        resp_a = client.get("/api/songs?channel_id=chan-a")
        assert resp_a.status_code == 200
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in resp_a.text
        assert "<script>alert(1)</script>" not in resp_a.text
        assert "Artist &amp; Co" in resp_a.text
        assert "Channel B Song" not in resp_a.text

    @pytest.mark.asyncio
    async def test_export_m3u_and_stats_and_channels(self, client):
        await insert_song(
            title="Exported Track",
            artist="Exported Artist",
            duration=240_000,
            links=[
                SongLink(
                    platform=Platform.YOUTUBE,
                    original_url="https://youtu.be/abc",
                    canonical_url="https://youtu.be/abc",
                    track_id="abc",
                )
            ],
            added_by="user1",
            channel_id="chan-x",
        )
        m3u_resp = client.get("/api/export.m3u?channel_id=chan-x")
        assert m3u_resp.status_code == 200
        assert "audio/x-mpegurl" in m3u_resp.headers["content-type"]
        assert "#EXTM3U" in m3u_resp.text
        assert "#EXTINF:240,Exported Artist - Exported Track" in m3u_resp.text
        assert "https://youtu.be/abc" in m3u_resp.text

        stats_resp = client.get("/api/stats?channel_id=chan-x")
        assert stats_resp.status_code == 200
        stats = stats_resp.json()
        assert stats["total_songs"] == 1
        assert stats["platforms"]["youtube"] == 1

        ch_resp = client.get("/api/channels")
        assert ch_resp.status_code == 200
        assert "chan-x" in ch_resp.json()["channels"]


class TestDiscordAuthAndVoting:
    def test_auth_me_unauthenticated(self, client):
        resp = client.get("/auth/me")
        assert resp.status_code == 200
        assert resp.json() == {"authenticated": False, "user": None}

    def test_auth_me_authenticated_and_logout(self, client):
        from playlistbot.web import SESSION_COOKIE, _encode_session

        token = _encode_session(
            {"id": "42", "username": "alice", "avatar_url": "https://example.com/a.png"}
        )
        client.cookies.set(SESSION_COOKIE, token)
        resp = client.get("/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is True
        assert data["user"]["username"] == "alice"

        index_resp = client.get("/")
        assert index_resp.status_code == 200
        assert "alice" in index_resp.text

        logout_resp = client.get("/auth/logout", follow_redirects=False)
        assert logout_resp.status_code == 302

    def test_decode_session_rejects_tampered_or_expired_token(self):
        import time

        from playlistbot.web import _decode_session, _encode_session

        valid = _encode_session({"id": "1", "username": "u"})
        assert _decode_session(valid + "tampered") is None
        assert _decode_session("invalid-no-dot") is None

        expired = _encode_session({"id": "1", "username": "u", "exp": int(time.time()) - 10})
        assert _decode_session(expired) is None

    def test_discord_login_redirect_and_missing_config(self, client, monkeypatch):
        monkeypatch.delenv("DISCORD_CLIENT_ID", raising=False)
        resp_unconfigured = client.get("/auth/discord", follow_redirects=False)
        assert resp_unconfigured.status_code == 503

        monkeypatch.setenv("DISCORD_CLIENT_ID", "client-123")
        resp = client.get("/auth/discord", follow_redirects=False)
        assert resp.status_code == 302
        assert "discord.com/api/oauth2/authorize" in resp.headers["location"]
        assert "client_id=client-123" in resp.headers["location"]

    def test_discord_callback_state_validation_and_success(self, client, monkeypatch):
        from unittest.mock import AsyncMock

        import playlistbot.web as webmod

        resp_bad = client.get("/auth/discord/callback?code=abc&state=wrong", follow_redirects=False)
        assert resp_bad.status_code == 400

        client.cookies.set(webmod.STATE_COOKIE, "state-xyz")
        monkeypatch.setattr(webmod, "_exchange_discord_code", AsyncMock(return_value=None))
        resp_fail = client.get(
            "/auth/discord/callback?code=abc&state=state-xyz", follow_redirects=False
        )
        assert resp_fail.status_code == 502

        client.cookies.set(webmod.STATE_COOKIE, "state-xyz")
        monkeypatch.setattr(
            webmod,
            "_exchange_discord_code",
            AsyncMock(
                return_value={
                    "id": "99",
                    "username": "dj_test",
                    "avatar_url": "https://cdn.discordapp.com/embed/avatars/1.png",
                }
            ),
        )
        resp_ok = client.get(
            "/auth/discord/callback?code=abc&state=state-xyz", follow_redirects=False
        )
        assert resp_ok.status_code == 302
        assert webmod.SESSION_COOKIE in resp_ok.cookies

    @pytest.mark.asyncio
    async def test_exchange_discord_code_http(self, monkeypatch):
        import httpx
        import respx

        from playlistbot.web import _exchange_discord_code

        monkeypatch.setenv("DISCORD_CLIENT_ID", "cid")
        monkeypatch.setenv("DISCORD_CLIENT_SECRET", "csecret")

        with respx.mock:
            respx.post("https://discord.com/api/oauth2/token").mock(
                return_value=httpx.Response(200, json={"access_token": "tok-123"})
            )
            respx.get("https://discord.com/api/users/@me").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "id": "777",
                        "username": "voter",
                        "global_name": "Voter Display",
                        "avatar": "avhash",
                    },
                )
            )
            user = await _exchange_discord_code("code-1", "http://localhost/cb")
            assert user is not None
            assert user["id"] == "777"
            assert user["username"] == "Voter Display"
            assert "avhash.png" in user["avatar_url"]

    @pytest.mark.asyncio
    async def test_vote_requires_auth_and_toggles_and_sorts(self, client):
        from playlistbot.web import SESSION_COOKIE, _encode_session

        s1 = await insert_song(
            title="Song One",
            artist="Artist 1",
            links=[
                SongLink(
                    platform=Platform.YOUTUBE,
                    original_url="https://youtu.be/1",
                    canonical_url="https://youtu.be/1",
                    track_id="1",
                )
            ],
        )
        s2 = await insert_song(
            title="Song Two",
            artist="Artist 2",
            links=[
                SongLink(
                    platform=Platform.SPOTIFY,
                    original_url="https://open.spotify.com/track/2",
                    canonical_url="https://open.spotify.com/track/2",
                    track_id="2",
                )
            ],
        )

        unauth = client.post(f"/api/songs/{s1}/vote")
        assert unauth.status_code == 401

        token = _encode_session(
            {"id": "u-1", "username": "voter1", "avatar_url": "https://example.com/1.png"}
        )
        client.cookies.set(SESSION_COOKIE, token)

        vote_on = client.post(f"/api/songs/{s1}/vote")
        assert vote_on.status_code == 200
        assert vote_on.json() == {"song_id": s1, "voted": True, "vote_count": 1}

        # HTMX request returns rendered vote button fragment
        htmx_resp = client.post(f"/api/songs/{s2}/vote", headers={"HX-Request": "true"})
        assert htmx_resp.status_code == 200
        assert 'class="vote-btn voted"' in htmx_resp.text
        assert 'data-votes="1"' in htmx_resp.text

        # Toggle s1 off so s2 has 1 vote and s1 has 0 votes
        vote_off = client.post(f"/api/songs/{s1}/vote")
        assert vote_off.status_code == 200
        assert vote_off.json() == {"song_id": s1, "voted": False, "vote_count": 0}

        # Sort by top voted puts Song Two before Song One
        sorted_resp = client.get("/api/songs?sort=votes")
        assert sorted_resp.status_code == 200
        assert sorted_resp.text.index("Song Two") < sorted_resp.text.index("Song One")

"""Tests for multi-server profiles, zero-latency config cache, bot invite, and web config."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import playlistbot.db as dbmod
from playlistbot.db import (
    get_channels,
    get_songs_newest_first,
    get_stats,
    init_db,
    insert_song,
)
from playlistbot.metadata import TrackInfo
from playlistbot.models import Platform, SongLink
from playlistbot.web import SESSION_COOKIE, _encode_session, app


@pytest.fixture(autouse=True)
async def setup_db(tmp_path, monkeypatch):
    """Initialize an isolated SQLite DB and clear the server cache for each test."""
    import os

    db_path = str(tmp_path / "test_servers.db")
    monkeypatch.setattr(dbmod, "DB_PATH", db_path)
    os.environ["DB_PATH"] = db_path
    await init_db()
    try:
        from playlistbot.servers import invalidate_server_cache

        invalidate_server_cache()
    except ImportError:
        pass
    yield
    if await asyncio.to_thread(os.path.exists, db_path):
        await asyncio.to_thread(os.remove, db_path)


@pytest.fixture
def client():
    return TestClient(app)


class TestServerProfileCacheAndPersistence:
    @pytest.mark.asyncio
    async def test_default_profile_and_upsert(self):
        from playlistbot.servers import get_server_profile, upsert_server_profile

        default_prof = await get_server_profile("guild-100")
        assert default_prof["guild_id"] == "guild-100"
        assert default_prof["allowed_channels"] == []
        assert default_prof["enabled_platforms"] == []
        assert default_prof["auto_sync_playlists"] is True
        assert default_prof["thread_card_enabled"] is True

        updated = await upsert_server_profile(
            guild_id="guild-100",
            name="Synthwave Club",
            icon_url="https://cdn.discordapp.com/icons/100/abc.png",
            allowed_channels=["ch-1", "ch-2"],
            enabled_platforms=["youtube", "spotify"],
            auto_sync_playlists=False,
            thread_card_enabled=False,
            playlist_prefix="Synthwave Queue",
        )
        assert updated["name"] == "Synthwave Club"
        assert updated["allowed_channels"] == ["ch-1", "ch-2"]
        assert updated["enabled_platforms"] == ["youtube", "spotify"]
        assert updated["auto_sync_playlists"] is False
        assert updated["thread_card_enabled"] is False
        assert updated["playlist_prefix"] == "Synthwave Queue"

    @pytest.mark.asyncio
    async def test_cached_profile_avoids_repeated_db_reads_and_invalidates_on_upsert(self):
        import playlistbot.servers as srvmod
        from playlistbot.servers import (
            get_server_profile_cached,
            invalidate_server_cache,
            upsert_server_profile,
        )

        await upsert_server_profile(
            guild_id="guild-fast",
            name="Fast Guild",
            allowed_channels=["c-fast"],
        )
        invalidate_server_cache("guild-fast")

        with patch.object(
            srvmod, "get_server_profile", wraps=srvmod.get_server_profile
        ) as spy_fetch:
            p1 = await get_server_profile_cached("guild-fast")
            p2 = await get_server_profile_cached("guild-fast")
            p3 = await get_server_profile_cached("guild-fast")
            assert p1["allowed_channels"] == ["c-fast"]
            assert p2["allowed_channels"] == ["c-fast"]
            assert p3["allowed_channels"] == ["c-fast"]
            # Only 1 DB query across 3 hot-path calls
            assert spy_fetch.call_count == 1

            # Upserting immediately updates/invalidates the cache
            await upsert_server_profile(
                guild_id="guild-fast",
                name="Fast Guild",
                allowed_channels=["c-fast", "c-new"],
            )
            p4 = await get_server_profile_cached("guild-fast")
            assert p4["allowed_channels"] == ["c-fast", "c-new"]


class TestGuildChannelsAndSongIsolation:
    @pytest.mark.asyncio
    async def test_sync_guild_channels_and_per_guild_queries(self):
        from playlistbot.servers import get_guild_channels, sync_guild_channels

        await sync_guild_channels(
            "guild-a",
            [
                {"id": "10", "name": "general"},
                {"id": "20", "name": "music-drops"},
            ],
        )
        channels = await get_guild_channels("guild-a")
        assert len(channels) == 2
        assert {c["name"] for c in channels} == {"general", "music-drops"}

        await insert_song(
            title="Alpha Song",
            artist="Alpha Artist",
            links=[
                SongLink(
                    platform=Platform.SPOTIFY,
                    original_url="https://open.spotify.com/track/a1",
                    canonical_url="https://open.spotify.com/track/a1",
                    track_id="a1",
                )
            ],
            channel_id="20",
            guild_id="guild-a",
        )
        await insert_song(
            title="Beta Song",
            artist="Beta Artist",
            links=[
                SongLink(
                    platform=Platform.YOUTUBE,
                    original_url="https://youtu.be/b1",
                    canonical_url="https://youtu.be/b1",
                    track_id="b1",
                )
            ],
            channel_id="99",
            guild_id="guild-b",
        )

        songs_a = await get_songs_newest_first(guild_id="guild-a")
        assert len(songs_a) == 1
        assert songs_a[0]["title"] == "Alpha Song"
        assert songs_a[0]["guild_id"] == "guild-a"

        stats_a = await get_stats(guild_id="guild-a")
        assert stats_a["total_songs"] == 1
        assert stats_a["platforms"] == {"spotify": 1}

        chans_a = await get_channels(guild_id="guild-a")
        assert "20" in chans_a
        assert "99" not in chans_a


class TestUserGuildPermissions:
    @pytest.mark.asyncio
    async def test_save_user_guilds_and_permission_bitmask(self, monkeypatch):
        from playlistbot.servers import can_user_manage_guild, save_user_guilds

        await save_user_guilds(
            "user-55",
            [
                {
                    "id": "g-manage",
                    "name": "Managed Server",
                    "icon": "icon1",
                    "permissions": str(0x20),  # MANAGE_GUILD
                    "owner": False,
                },
                {
                    "id": "g-owner",
                    "name": "Owned Server",
                    "icon": None,
                    "permissions": "0",
                    "owner": True,
                },
                {
                    "id": "g-member",
                    "name": "Read Only Server",
                    "icon": None,
                    "permissions": str(0x400),  # VIEW_CHANNEL only
                    "owner": False,
                },
            ],
        )

        user = {"id": "user-55", "username": "mod"}
        assert await can_user_manage_guild(user, "g-manage") is True
        assert await can_user_manage_guild(user, "g-owner") is True
        assert await can_user_manage_guild(user, "g-member") is False
        assert await can_user_manage_guild(None, "g-manage") is False

        monkeypatch.setenv("BOT_ADMIN_IDS", "user-55")
        assert await can_user_manage_guild(user, "g-member") is True


class TestBotMultiServerBehavior:
    @pytest.mark.asyncio
    async def test_bot_enforces_server_profile_channels_platforms_and_flags(self):
        import playlistbot.bot as botmod
        from playlistbot.servers import upsert_server_profile

        await upsert_server_profile(
            guild_id="777",
            name="Strict Server",
            allowed_channels=["500"],
            enabled_platforms=["spotify"],
            auto_sync_playlists=False,
            thread_card_enabled=False,
        )

        # Message in non-allowed channel 501 is ignored
        msg_blocked = MagicMock()
        msg_blocked.author.bot = False
        msg_blocked.guild = SimpleNamespace(id=777, name="Strict Server")
        msg_blocked.channel.id = 501
        msg_blocked.content = "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"

        with patch.object(botmod.bot, "process_commands", new_callable=AsyncMock) as proc_mock:
            await botmod.on_message(msg_blocked)
            proc_mock.assert_not_called()

        # Message in allowed channel 500 with both YouTube and Spotify links:
        # Only Spotify is processed, no thread card is created, and no external playlist sync runs
        msg_ok = MagicMock()
        msg_ok.id = 9001
        msg_ok.author.bot = False
        msg_ok.guild = SimpleNamespace(id=777, name="Strict Server")
        msg_ok.channel = MagicMock()
        msg_ok.channel.id = 500
        msg_ok.content = (
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ "
            "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"
        )

        fake_meta = TrackInfo(
            title="Filtered Track",
            artist="Filtered Artist",
            source_url="https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT",
            source_platform=Platform.SPOTIFY,
        )

        with (
            patch.object(botmod.bot, "process_commands", new_callable=AsyncMock),
            patch.object(
                botmod, "extract_metadata", AsyncMock(return_value=fake_meta)
            ) as meta_mock,
            patch.object(botmod, "_get_or_create_thread", new_callable=AsyncMock) as thread_mock,
            patch.object(botmod.playlist_mgr, "add_to_playlist") as sync_mock,
        ):
            await botmod.on_message(msg_ok)
            assert meta_mock.call_count == 1
            thread_mock.assert_not_called()
            sync_mock.assert_not_called()

        songs = await get_songs_newest_first(guild_id="777")
        assert len(songs) == 1
        assert songs[0]["title"] == "Filtered Track"

    @pytest.mark.asyncio
    async def test_on_guild_join_registers_guild_and_channels(self):
        import playlistbot.bot as botmod
        from playlistbot.servers import get_guild_channels, get_server_profile

        fake_guild = SimpleNamespace(
            id=888,
            name="Joined Guild",
            icon=SimpleNamespace(url="https://cdn.discordapp.com/icons/888/x.png"),
            text_channels=[
                SimpleNamespace(id=11, name="tracks"),
                SimpleNamespace(id=12, name="lounge"),
            ],
        )
        await botmod.on_guild_join(fake_guild)
        prof = await get_server_profile("888")
        assert prof["name"] == "Joined Guild"
        chans = await get_guild_channels("888")
        assert {c["name"] for c in chans} == {"tracks", "lounge"}


class TestWebServerConfigAndInviteApi:
    def test_invite_endpoints(self, client, monkeypatch):
        monkeypatch.delenv("DISCORD_CLIENT_ID", raising=False)
        assert client.get("/api/invite").status_code == 503

        monkeypatch.setenv("DISCORD_CLIENT_ID", "app-999")
        resp = client.get("/api/invite?guild_id=12345")
        assert resp.status_code == 200
        data = resp.json()
        assert "client_id=app-999" in data["invite_url"]
        assert "scope=bot+applications.commands" in data["invite_url"]
        assert "guild_id=12345" in data["invite_url"]
        assert "disable_guild_select=true" in data["invite_url"]

        redir = client.get("/invite?guild_id=12345", follow_redirects=False)
        assert redir.status_code == 302
        assert "discord.com/oauth2/authorize" in redir.headers["location"]

    @pytest.mark.asyncio
    async def test_server_config_auth_validation_and_persistence(self, client):
        from playlistbot.servers import save_user_guilds, sync_guild_channels

        await sync_guild_channels(
            "guild-42",
            [{"id": "888", "name": "music-share"}, {"id": "889", "name": "chat"}],
        )
        await save_user_guilds(
            "admin-1",
            [
                {
                    "id": "guild-42",
                    "name": "Studio 42",
                    "icon": None,
                    "permissions": str(0x20),
                    "owner": False,
                }
            ],
        )

        # Unauthenticated PUT -> 401
        unauth = client.put(
            "/api/servers/guild-42/config",
            json={"allowed_channels": ["888"]},
        )
        assert unauth.status_code == 401

        # Authenticated non-manager -> 403
        member_token = _encode_session({"id": "member-2", "username": "viewer"})
        client.cookies.set(SESSION_COOKIE, member_token)
        forbidden = client.put(
            "/api/servers/guild-42/config",
            json={"allowed_channels": ["888"]},
        )
        assert forbidden.status_code == 403

        # Authenticated manager -> can update & validate
        admin_token = _encode_session({"id": "admin-1", "username": "admin_user"})
        client.cookies.set(SESSION_COOKIE, admin_token)

        # Invalid platform rejected with 400
        bad_plat = client.put(
            "/api/servers/guild-42/config",
            json={"enabled_platforms": ["napster_invalid_xyz"]},
        )
        assert bad_plat.status_code == 400

        # Invalid channel ID rejected with 400
        bad_chan = client.put(
            "/api/servers/guild-42/config",
            json={"allowed_channels": ["bad channel id!"]},
        )
        assert bad_chan.status_code == 400

        # Valid config update succeeds
        ok_resp = client.put(
            "/api/servers/guild-42/config",
            json={
                "name": "Studio 42 HQ",
                "allowed_channels": ["888"],
                "enabled_platforms": ["youtube", "spotify", "deezer"],
                "auto_sync_playlists": True,
                "thread_card_enabled": False,
                "playlist_prefix": "Studio 42 Picks",
            },
        )
        assert ok_resp.status_code == 200
        cfg = ok_resp.json()
        assert cfg["name"] == "Studio 42 HQ"
        assert cfg["allowed_channels"] == ["888"]
        assert cfg["enabled_platforms"] == ["youtube", "spotify", "deezer"]
        assert cfg["thread_card_enabled"] is False
        assert cfg["playlist_prefix"] == "Studio 42 Picks"

        # GET /api/servers/{guild_id}/config returns profile + available_channels + can_manage
        get_cfg = client.get("/api/servers/guild-42/config")
        assert get_cfg.status_code == 200
        got = get_cfg.json()
        assert got["can_manage"] is True
        assert len(got["available_channels"]) == 2

        # GET /api/servers lists Studio 42 HQ with can_manage=True
        srv_list = client.get("/api/servers")
        assert srv_list.status_code == 200
        servers = srv_list.json()["servers"]
        assert any(s["guild_id"] == "guild-42" and s["can_manage"] is True for s in servers)

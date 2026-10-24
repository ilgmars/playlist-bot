"""TDD tests for multi-platform playlists, server visibility, and admin editing (#12, #13)."""

from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

from playlistbot import db
from playlistbot.models import Platform, SongLink


@pytest.fixture(autouse=True)
async def test_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_playlists_multi.db")
    monkeypatch.setattr(db, "DB_PATH", db_file)
    os.environ["DB_PATH"] = db_file
    await db.init_db()
    try:
        from playlistbot.servers import invalidate_server_cache

        invalidate_server_cache()
    except ImportError:
        pass
    yield db_file


async def _seed_multi_platform_songs() -> tuple[str, str, str]:
    s1 = await db.insert_song(
        title="Around the World",
        artist="Daft Punk",
        links=[
            SongLink(
                platform=Platform.SPOTIFY,
                original_url="https://open.spotify.com/track/1pKYYY0dkg23sQQXi0Q5zN",
                canonical_url="https://open.spotify.com/track/1pKYYY0dkg23sQQXi0Q5zN",
                track_id="1pKYYY0dkg23sQQXi0Q5zN",
            ),
            SongLink(
                platform=Platform.YOUTUBE,
                original_url="https://www.youtube.com/watch?v=K0HSD_i2DvA",
                canonical_url="https://www.youtube.com/watch?v=K0HSD_i2DvA",
                track_id="K0HSD_i2DvA",
            ),
            SongLink(
                platform=Platform.APPLE_MUSIC,
                original_url="https://music.apple.com/us/album/around-the-world/697194953?i=697195787",
                canonical_url="https://music.apple.com/us/album/around-the-world/697194953?i=697195787",
            ),
            SongLink(
                platform=Platform.DEEZER,
                original_url="https://www.deezer.com/track/3135553",
                canonical_url="https://www.deezer.com/track/3135553",
            ),
        ],
        added_by="u1",
        channel_id="1001",
        guild_id="9001",
    )
    s2 = await db.insert_song(
        title="One More Time",
        artist="Daft Punk",
        links=[
            SongLink(
                platform=Platform.SPOTIFY,
                original_url="https://open.spotify.com/track/0DiWol3AO6WpXZgp0goxAV",
                canonical_url="https://open.spotify.com/track/0DiWol3AO6WpXZgp0goxAV",
                track_id="0DiWol3AO6WpXZgp0goxAV",
            ),
            SongLink(
                platform=Platform.YOUTUBE_MUSIC,
                original_url="https://music.youtube.com/watch?v=FGBhQbmPwH8",
                canonical_url="https://music.youtube.com/watch?v=FGBhQbmPwH8",
                track_id="FGBhQbmPwH8",
            ),
            SongLink(
                platform=Platform.SOUND_CLOUD,
                original_url="https://soundcloud.com/daftpunk/one-more-time",
                canonical_url="https://soundcloud.com/daftpunk/one-more-time",
            ),
            SongLink(
                platform=Platform.TIDAL,
                original_url="https://tidal.com/browse/track/123456",
                canonical_url="https://tidal.com/browse/track/123456",
            ),
        ],
        added_by="u2",
        channel_id="1001",
        guild_id="9001",
    )
    s3 = await db.insert_song(
        title="Master of Puppets",
        artist="Metallica",
        links=[
            SongLink(
                platform=Platform.SPOTIFY,
                original_url="https://open.spotify.com/track/2MuWTIM3b0YEAskbeeFE1i",
                canonical_url="https://open.spotify.com/track/2MuWTIM3b0YEAskbeeFE1i",
                track_id="2MuWTIM3b0YEAskbeeFE1i",
            ),
        ],
        added_by="u9",
        channel_id="2002",
        guild_id="9002",
    )
    return s1, s2, s3


class TestMultiPlatformPlaylistsBuilder:
    async def test_build_platform_playlists_generates_youtube_queue_and_spotify_uris(self):
        from playlistbot.playlist import build_platform_playlists

        await _seed_multi_platform_songs()
        songs = await db.get_songs_newest_first(limit=50, guild_id="9001")
        playlists = build_platform_playlists(songs, guild_id="9001", channel_id="1001")

        assert "spotify" in playlists
        assert "youtube" in playlists
        assert "youtube_music" in playlists
        assert "apple_music" in playlists
        assert "deezer" in playlists
        assert "soundcloud" in playlists
        assert "tidal" in playlists

        sp = playlists["spotify"]
        assert sp["track_count"] == 2
        assert "spotify:track:0DiWol3AO6WpXZgp0goxAV" in sp["track_uris"]
        assert "spotify:track:1pKYYY0dkg23sQQXi0Q5zN" in sp["track_uris"]
        assert "platform=spotify" in sp["export_m3u_url"]

        yt = playlists["youtube"]
        assert yt["track_count"] == 2
        assert yt["mode"] == "instant_queue"
        assert "watch_videos?video_ids=" in yt["open_url"]
        assert "FGBhQbmPwH8" in yt["open_url"]
        assert "K0HSD_i2DvA" in yt["open_url"]

        ytm = playlists["youtube_music"]
        assert ytm["track_count"] == 2
        assert "FGBhQbmPwH8" in ytm["open_url"]

    async def test_synced_and_custom_server_playlist_urls_take_precedence(self):
        from playlistbot.playlist import build_platform_playlists

        await _seed_multi_platform_songs()
        await db.save_playlist_state(
            channel_id="1001",
            platform=Platform.SPOTIFY,
            playlist_id="37i9dQZF1DXcBWIGoYBM5M",
            playlist_url="https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
        )
        songs = await db.get_songs_newest_first(limit=50, guild_id="9001")
        synced = await db.get_playlists("1001")
        custom = {"apple_music": "https://music.apple.com/us/playlist/server-mix/pl.u-12345"}

        playlists = build_platform_playlists(
            songs,
            synced_playlists=synced,
            custom_urls=custom,
            guild_id="9001",
            channel_id="1001",
        )
        assert playlists["spotify"]["mode"] == "synced"
        assert (
            playlists["spotify"]["open_url"]
            == "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
        )
        assert playlists["apple_music"]["mode"] == "synced"
        assert (
            playlists["apple_music"]["open_url"]
            == "https://music.apple.com/us/playlist/server-mix/pl.u-12345"
        )


class TestServerScopedPlaylistVisibility:
    async def test_user_only_sees_playlists_from_servers_they_belong_to(self):
        from playlistbot.servers import save_user_guilds, upsert_server_profile
        from playlistbot.web import SESSION_COOKIE, _encode_session, app

        await _seed_multi_platform_songs()
        await upsert_server_profile(
            guild_id="9001",
            name="Synthwave Guild",
            playlist_urls={"spotify": "https://open.spotify.com/playlist/5ABHKGoOzxkaa28ttQV9sE"},
        )
        await upsert_server_profile(
            guild_id="9002",
            name="Secret Metal Guild",
            playlist_urls={"spotify": "https://open.spotify.com/playlist/9999999999999999999999"},
        )
        await save_user_guilds(
            "member-9001",
            [{"id": "9001", "name": "Synthwave Guild", "permissions": "0", "owner": False}],
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Unauthenticated request for a specific guild's playlists -> 401
            r_unauth = await client.get("/api/playlists?guild_id=9001")
            assert r_unauth.status_code == 401

            # Authenticate as member of 9001 only
            client.cookies.set(
                SESSION_COOKIE,
                _encode_session({"id": "member-9001", "username": "synth_fan"}),
            )

            # 1. GET /api/servers only returns 9001, not 9002
            r_srvs = await client.get("/api/servers")
            assert r_srvs.status_code == 200
            srv_ids = [s["guild_id"] for s in r_srvs.json()["servers"]]
            assert srv_ids == ["9001"]

            # 2. GET /api/playlists (no guild_id) only includes tracks from 9001, not 9002
            r_all = await client.get("/api/playlists")
            assert r_all.status_code == 200
            sp_all = r_all.json()["playlists"]["spotify"]
            assert sp_all["track_count"] == 2
            assert "spotify:track:2MuWTIM3b0YEAskbeeFE1i" not in sp_all["track_uris"]

            # 3. GET /api/playlists?guild_id=9001 succeeds with can_edit=False
            r_own = await client.get("/api/playlists?guild_id=9001&channel_id=1001")
            assert r_own.status_code == 200
            data_own = r_own.json()
            assert data_own["can_edit"] is False
            assert data_own["playlists"]["spotify"]["track_count"] == 2

            # 4. Accessing playlists or songs of guild 9002 (not a member) -> 403 Forbidden
            assert (await client.get("/api/playlists?guild_id=9002")).status_code == 403
            assert (
                await client.get("/playlists/spotify?guild_id=9002", follow_redirects=False)
            ).status_code == 403
            assert (
                await client.get("/api/playlists/spotify/tracks.txt?guild_id=9002")
            ).status_code == 403
            assert (await client.get("/api/songs?guild_id=9002")).status_code == 403

            # 5. Member of 9001 can open redirects, tracks.txt, and export.m3u for 9001
            r_yt = await client.get(
                "/playlists/youtube?guild_id=9001&channel_id=1001", follow_redirects=False
            )
            assert r_yt.status_code == 302
            assert "youtube.com/watch_videos?video_ids=" in r_yt.headers["location"]

            r_txt = await client.get("/api/playlists/spotify/tracks.txt?guild_id=9001")
            assert r_txt.status_code == 200
            assert "spotify:track:0DiWol3AO6WpXZgp0goxAV" in r_txt.text

            r_m3u = await client.get("/api/export.m3u?guild_id=9001&platform=spotify")
            assert r_m3u.status_code == 200
            assert "open.spotify.com/track/" in r_m3u.text
            assert "playlist-spotify.m3u" in r_m3u.headers.get("content-disposition", "")


class TestServerAdminPlaylistEditing:
    async def test_server_admin_can_edit_playlist_urls_and_add_edit_delete_songs(self):
        from playlistbot.servers import save_user_guilds, upsert_server_profile
        from playlistbot.web import SESSION_COOKIE, _encode_session, app

        s1, _, _ = await _seed_multi_platform_songs()
        await upsert_server_profile(guild_id="9001", name="Synthwave Guild")
        await save_user_guilds(
            "member-1",
            [{"id": "9001", "name": "Synthwave Guild", "permissions": "0", "owner": False}],
        )
        await save_user_guilds(
            "admin-1",
            [
                {
                    "id": "9001",
                    "name": "Synthwave Guild",
                    "permissions": str(0x8),  # ADMINISTRATOR
                    "owner": False,
                }
            ],
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Non-admin member cannot edit playlists, add songs, edit songs, or delete songs -> 403
            client.cookies.set(
                SESSION_COOKIE,
                _encode_session({"id": "member-1", "username": "regular_member"}),
            )
            assert (
                await client.put(
                    "/api/servers/9001/playlists",
                    json={"playlist_prefix": "Hacked"},
                )
            ).status_code == 403
            assert (
                await client.post(
                    "/api/servers/9001/songs",
                    json={
                        "title": "Nightcall",
                        "artist": "Kavinsky",
                        "url": "https://open.spotify.com/track/0U0ldCRmgCqhVvD6ksG63j",
                    },
                )
            ).status_code == 403
            assert (
                await client.patch(
                    f"/api/songs/{s1}",
                    json={"title": "Edited by non-admin"},
                )
            ).status_code == 403
            assert (await client.delete(f"/api/songs/{s1}")).status_code == 403

            # Server admin CAN edit playlist settings, add songs, edit songs, and delete songs
            client.cookies.set(
                SESSION_COOKIE,
                _encode_session({"id": "admin-1", "username": "guild_admin"}),
            )
            r_pl = await client.get("/api/playlists?guild_id=9001")
            assert r_pl.status_code == 200
            assert r_pl.json()["can_edit"] is True

            # 1. Edit server playlist URLs and prefix via PUT /api/servers/9001/playlists
            r_edit_pl = await client.put(
                "/api/servers/9001/playlists",
                json={
                    "playlist_prefix": "Synthwave Classics",
                    "playlist_urls": {
                        "spotify": "https://open.spotify.com/playlist/37i9dQZF1DXdLEN7aqioXM",
                    },
                },
            )
            assert r_edit_pl.status_code == 200
            assert r_edit_pl.json()["playlist_prefix"] == "Synthwave Classics"
            assert (
                r_edit_pl.json()["playlist_urls"]["spotify"]
                == "https://open.spotify.com/playlist/37i9dQZF1DXdLEN7aqioXM"
            )

            # 2. Add a track to the server playlist via POST /api/servers/9001/songs
            r_add = await client.post(
                "/api/servers/9001/songs",
                json={
                    "title": "Nightcall",
                    "artist": "Kavinsky",
                    "url": "https://open.spotify.com/track/0U0ldCRmgCqhVvD6ksG63j",
                    "channel_id": "1001",
                },
            )
            assert r_add.status_code == 200
            added_song_id = r_add.json()["id"]

            # Verify playlist now has 3 Spotify tracks
            r_after_add = await client.get("/api/playlists?guild_id=9001")
            assert r_after_add.json()["playlists"]["spotify"]["track_count"] == 3

            # 3. Edit song title & artist via PATCH /api/songs/{song_id}
            r_patch = await client.patch(
                f"/api/songs/{added_song_id}",
                json={"title": "Nightcall (Drive Edit)", "artist": "Kavinsky ft. Lovefoxxx"},
            )
            assert r_patch.status_code == 200
            assert r_patch.json()["title"] == "Nightcall (Drive Edit)"
            assert r_patch.json()["artist"] == "Kavinsky ft. Lovefoxxx"

            # 4. Delete song from playlist via DELETE /api/songs/{song_id}
            r_del = await client.delete(f"/api/songs/{added_song_id}")
            assert r_del.status_code == 200
            assert r_del.json()["deleted"] is True

            r_after_del = await client.get("/api/playlists?guild_id=9001")
            assert r_after_del.json()["playlists"]["spotify"]["track_count"] == 2

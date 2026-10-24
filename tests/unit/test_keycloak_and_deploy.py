"""Tests for Keycloak OIDC auth and install.sh."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from playlistbot import db as db_mod
from playlistbot.models import Platform, SongLink
from playlistbot.servers import invalidate_server_cache, upsert_server_profile
from playlistbot.web import SESSION_COOKIE, STATE_COOKIE, _decode_session, _encode_session, app

REPO_ROOT = Path(__file__).resolve().parents[2]
BASH_BIN = shutil.which("bash") or "/bin/bash"


@pytest.fixture
async def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = str(tmp_path / "test_keycloak.db")
    monkeypatch.setattr(db_mod, "DB_PATH", db_file)
    invalidate_server_cache()
    await db_mod.init_db()
    yield db_file
    invalidate_server_cache()


class TestKeycloakOIDCAuth:
    @pytest.mark.asyncio
    async def test_auth_keycloak_503_when_unconfigured(
        self,
        temp_db: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.delenv("KEYCLOAK_ISSUER_URL", raising=False)
        monkeypatch.delenv("KEYCLOAK_CLIENT_ID", raising=False)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/auth/keycloak")
            assert resp.status_code == 503
            assert "KEYCLOAK" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_auth_keycloak_redirects_to_realm_authorize(
        self,
        temp_db: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("KEYCLOAK_ISSUER_URL", "https://irbe.arma.lv/realms/playlist-bot")
        monkeypatch.setenv("KEYCLOAK_CLIENT_ID", "playlist-bot-web")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/auth/keycloak", follow_redirects=False)
            assert resp.status_code == 302
            loc = resp.headers["location"]
            assert loc.startswith(
                "https://irbe.arma.lv/realms/playlist-bot/protocol/openid-connect/auth?"
            )
            assert "client_id=playlist-bot-web" in loc
            assert "response_type=code" in loc
            assert STATE_COOKIE in resp.cookies

    @pytest.mark.asyncio
    async def test_auth_keycloak_callback_rejects_bad_state(
        self,
        temp_db: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("KEYCLOAK_ISSUER_URL", "https://irbe.arma.lv/realms/playlist-bot")
        monkeypatch.setenv("KEYCLOAK_CLIENT_ID", "playlist-bot-web")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/auth/keycloak/callback?code=abc&state=wrong",
                cookies={STATE_COOKIE: "expected"},
            )
            assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_auth_keycloak_callback_exchanges_code_and_maps_roles(
        self,
        temp_db: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("KEYCLOAK_ISSUER_URL", "https://irbe.arma.lv/realms/playlist-bot")
        monkeypatch.setenv("KEYCLOAK_CLIENT_ID", "playlist-bot-web")
        monkeypatch.setenv("KEYCLOAK_CLIENT_SECRET", "kc-secret-123")

        mock_tok_resp = MagicMock(status_code=200)
        mock_tok_resp.json.return_value = {"access_token": "kc-access-token"}

        mock_userinfo_resp = MagicMock(status_code=200)
        mock_userinfo_resp.json.return_value = {
            "sub": "kc-user-42",
            "preferred_username": "ilgmars",
            "name": "Ilgmars",
            "roles": ["playlist-admin", "server-admin:guild_rigas"],
            "guilds": ["guild_rigas", "guild_baltic"],
        }

        mock_http_client = AsyncMock()
        mock_http_client.__aenter__.return_value = mock_http_client
        mock_http_client.post.return_value = mock_tok_resp
        mock_http_client.get.return_value = mock_userinfo_resp

        with patch("playlistbot.keycloak.httpx.AsyncClient", return_value=mock_http_client):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/auth/keycloak/callback?code=valid_code&state=csrf_ok",
                    cookies={STATE_COOKIE: "csrf_ok"},
                    follow_redirects=False,
                )
                assert resp.status_code == 302
                assert resp.headers["location"] == "/"
                sess_cookie = resp.cookies.get(SESSION_COOKIE)
                assert sess_cookie is not None
                decoded = _decode_session(sess_cookie)
                assert decoded is not None
                assert decoded["id"] == "kc-user-42"
                assert decoded["username"] == "Ilgmars"
                assert decoded["auth_provider"] == "keycloak"
                assert decoded["is_admin"] is True
                assert "guild_rigas" in decoded["guilds"]
                assert "guild_rigas" in decoded["manage_guilds"]

    @pytest.mark.asyncio
    async def test_keycloak_admin_can_view_and_edit_server_playlists(self, temp_db: str):
        await upsert_server_profile(guild_id="guild_kc", name="Keycloak Managed Guild")
        await db_mod.insert_song(
            title="Strobe",
            artist="deadmau5",
            links=[
                SongLink(
                    platform=Platform.SPOTIFY,
                    original_url="https://open.spotify.com/track/7777777777777777777777",
                    canonical_url="https://open.spotify.com/track/7777777777777777777777",
                    track_id="7777777777777777777777",
                )
            ],
            guild_id="guild_kc",
        )
        kc_admin_cookie = _encode_session(
            {
                "id": "kc-admin-1",
                "username": "Ilgmars (Keycloak)",
                "auth_provider": "keycloak",
                "is_admin": True,
                "roles": ["playlist-admin"],
                "guilds": [],
                "manage_guilds": [],
            }
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Can view server playlists
            pl_resp = await client.get(
                "/api/playlists?guild_id=guild_kc",
                cookies={SESSION_COOKIE: kc_admin_cookie},
            )
            assert pl_resp.status_code == 200
            assert pl_resp.json()["can_edit"] is True

            # Can update server playlist URLs
            upd_resp = await client.put(
                "/api/servers/guild_kc/playlists",
                cookies={SESSION_COOKIE: kc_admin_cookie},
                json={
                    "playlist_prefix": "KC Official Mix",
                    "playlist_urls": {
                        "spotify": "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
                    },
                },
            )
            assert upd_resp.status_code == 200
            assert upd_resp.json()["playlist_prefix"] == "KC Official Mix"


class TestKeycloakCustomRealmAndDeployScript:
    def test_keycloak_custom_realm_json_is_valid(self):
        realm_file = REPO_ROOT / "deploy" / "keycloak" / "playlist-bot-realm.json"
        assert realm_file.exists(), "deploy/keycloak/playlist-bot-realm.json must exist"
        data = json.loads(realm_file.read_text(encoding="utf-8"))
        assert data["realm"] == "playlist-bot"
        assert data["enabled"] is True
        role_names = {r["name"] for r in data.get("roles", {}).get("realm", [])}
        assert "playlist-admin" in role_names
        assert "playlist-member" in role_names
        clients = {c["clientId"]: c for c in data.get("clients", [])}
        assert "playlist-bot-web" in clients
        idps = {idp["alias"]: idp for idp in data.get("identityProviders", [])}
        assert "discord" in idps

    def test_install_sh_exists_and_passes_syntax_and_help(self):
        script = REPO_ROOT / "install.sh"
        assert script.exists(), "install.sh must exist at repository root"
        mode = script.stat().st_mode
        assert bool(mode & stat.S_IXUSR), "install.sh must be executable"

        syntax = subprocess.run(  # noqa: S603
            [BASH_BIN, "-n", str(script)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert syntax.returncode == 0, syntax.stderr

        help_res = subprocess.run(  # noqa: S603
            [BASH_BIN, str(script), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert help_res.returncode == 0
        assert "Usage:" in help_res.stdout
        assert "Cloudflare" in help_res.stdout or "Keycloak" in help_res.stdout

    def test_install_sh_non_interactive_generates_env_and_shows_guides(self, tmp_path: Path):
        script = REPO_ROOT / "install.sh"
        env_out = tmp_path / ".env.generated"
        env = {
            **os.environ,
            "DISCORD_TOKEN": "MTIzNDU2Nzg5.test.token",
            "DISCORD_CLIENT_ID": "123456789012345678",
            "DISCORD_CLIENT_SECRET": "discord-secret-xyz",
            "PUBLIC_URL": "https://music.example.com",
            "BOT_ADMIN_IDS": "987654321098765432",
            "ENABLE_CLOUDFLARE_TUNNEL": "y",
            "CF_TUNNEL_TOKEN": "eyJhIjoiY2ZfdG9rZW5fdGVzdCJ9",
            "AUTH_MODE": "both",
            "KEYCLOAK_MODE": "bundled",
            "KEYCLOAK_ADMIN_PASSWORD": "kc-admin-pass-test",
            "KEYCLOAK_CLIENT_SECRET": "kc-web-secret-test",
        }
        res = subprocess.run(  # noqa: S603
            [
                BASH_BIN,
                str(script),
                "--non-interactive",
                "--dry-run",
                "--env-file",
                str(env_out),
            ],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert res.returncode == 0, f"install.sh failed: {res.stderr}\n{res.stdout}"
        # Check that guides and direct URLs are shown during installation
        assert "https://discord.com/developers/applications" in res.stdout
        assert "https://one.dash.cloudflare.com" in res.stdout
        assert "playlist-bot" in res.stdout

        assert env_out.exists()
        content = env_out.read_text(encoding="utf-8")
        assert "DISCORD_TOKEN=MTIzNDU2Nzg5.test.token" in content
        assert "CF_TUNNEL_TOKEN=eyJhIjoiY2ZfdG9rZW5fdGVzdCJ9" in content
        assert "KEYCLOAK_REALM=playlist-bot" in content
        assert "KEYCLOAK_CLIENT_ID=playlist-bot-web" in content
        assert "SESSION_SECRET=" in content

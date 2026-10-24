"""Web dashboard — FastAPI backend with Jinja2 + HTMX template rendering."""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import secrets  # cryptographic nonce generator
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from .db import get_channels, get_songs_newest_first, get_stats, toggle_vote
from .playlist import generate_m3u
from .servers import (
    build_bot_invite_url,
    can_user_manage_guild,
    get_guild_channels,
    get_server_profile,
    is_valid_id,
    list_servers,
    save_user_guilds,
    upsert_server_profile,
    validate_server_config_payload,
)

app = FastAPI(title="Playlist Bot Dashboard", version="0.1.0")

_template_dir = Path(__file__).resolve().parent / "templates"
_static_dir = Path(__file__).resolve().parent / "static"
SESSION_COOKIE = "playlistbot_session"
STATE_COOKIE = "playlistbot_oauth_state"
_FALLBACK_KEY = secrets.token_hex(32)  # ephemeral signing key when SESSION_KEY unset

if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

templates = Jinja2Templates(directory=str(_template_dir))

_PLATFORM_LABELS: dict[str, str] = {
    "youtube": "YouTube",
    "youtube_music": "YT Music",
    "spotify": "Spotify",
    "apple_music": "Apple Music",
    "soundcloud": "SoundCloud",
    "bandcamp": "Bandcamp",
    "tidal": "Tidal",
    "deezer": "Deezer",
}


def _signing_key() -> bytes:
    raw = (
        os.environ.get("SESSION_SECRET") or os.environ.get("DISCORD_CLIENT_SECRET") or _FALLBACK_KEY
    )
    return raw.encode("utf-8")


def _encode_session(user: dict[str, Any]) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(user).encode("utf-8")).decode("ascii")
    sig = hmac.new(_signing_key(), payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _decode_session(cookie_val: str | None) -> dict[str, Any] | None:
    if not cookie_val or "." not in cookie_val:
        return None
    payload, sig = cookie_val.rsplit(".", 1)
    expected = hmac.new(_signing_key(), payload.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
        if not isinstance(data, dict) or not data.get("id"):
            return None
        exp = data.get("exp")
        if exp is not None and int(exp) < int(time.time()):
            return None
        return data
    except Exception:
        return None


def _get_current_user(request: Request) -> dict[str, Any] | None:
    return _decode_session(request.cookies.get(SESSION_COOKIE))


def _render_vote_button(
    song_id: str,
    vote_count: int,
    voted: bool,
    is_logged_in: bool,
) -> str:
    """Render the upvote button fragment for a song card."""
    sid = html.escape(song_id, quote=True)
    active_cls = " voted" if voted else ""
    pressed = "true" if voted else "false"
    svg = (
        '<svg class="vote-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="m18 15-6-6-6 6"/></svg>'
    )
    if is_logged_in:
        return (
            f'<button type="button" class="vote-btn{active_cls}" '
            f'hx-post="/api/songs/{sid}/vote" hx-swap="outerHTML" '
            f'data-song-id="{sid}" data-voted="{pressed}" data-votes="{vote_count}" '
            f'aria-pressed="{pressed}" aria-label="Upvote song ({vote_count} votes)">'
            f'{svg}<span class="vote-count">{vote_count}</span></button>'
        )
    return (
        f'<button type="button" class="vote-btn" onclick="promptDiscordLogin()" '
        f'data-song-id="{sid}" data-voted="false" data-votes="{vote_count}" '
        f'aria-pressed="false" title="Log in with Discord to vote" '
        f'aria-label="Log in with Discord to vote ({vote_count} votes)">'
        f'{svg}<span class="vote-count">{vote_count}</span></button>'
    )


def _render_song_card(song: dict[str, Any], is_logged_in: bool = False) -> str:
    """Render a single song dictionary as an escaped HTML card fragment."""
    song_id = str(song.get("id", ""))
    title = html.escape(str(song.get("title", "")), quote=True)
    artist = html.escape(str(song.get("artist", "")), quote=True)
    created_at = html.escape(str(song.get("created_at", "")), quote=True)
    channel_id = html.escape(str(song.get("channel_id") or ""), quote=True)
    guild_id = html.escape(str(song.get("guild_id") or ""), quote=True)
    vote_count = int(song.get("vote_count") or 0)
    voted = bool(song.get("user_voted"))
    badges: list[str] = []
    for link in song.get("links", []):
        raw_plat = str(link.get("platform", "unknown"))
        plat = html.escape(raw_plat, quote=True)
        url = html.escape(str(link.get("original_url", "")), quote=True)
        label = html.escape(_PLATFORM_LABELS.get(raw_plat, raw_plat.capitalize()))
        badges.append(
            f'<a href="{url}" class="platform-badge {plat}" '
            f'data-platform="{plat}" target="_blank" rel="noopener">{label}</a>'
        )
    link_tags = " ".join(badges)
    vote_html = _render_vote_button(song_id, vote_count, voted, is_logged_in)
    return (
        f'<article class="song-card" data-channel="{channel_id}" '
        f'data-guild="{guild_id}" data-votes="{vote_count}">\n'
        f'  <div class="song-header">\n'
        f'    <div class="song-title-row">\n'
        f'      <strong class="song-title">{title}</strong>\n'
        f'      <div class="song-meta-right">{vote_html}'
        f'<time class="song-time">{created_at}</time></div>\n'
        f"    </div>\n"
        f'    <span class="song-artist">{artist}</span>\n'
        f"  </div>\n"
        f'  <div class="song-platforms song-links">{link_tags}</div>\n'
        f"</article>"
    )


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    channel_id: str | None = None,
    guild_id: str | None = None,
):
    """Main dashboard page showing the latest songs, newest first."""
    user = _get_current_user(request)
    songs = await get_songs_newest_first(
        limit=50,
        channel_id=channel_id,
        current_user_id=user["id"] if user else None,
        guild_id=guild_id,
    )
    channels = await get_channels(guild_id=guild_id)
    servers = await list_servers(user=user)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "songs": songs,
            "channels": channels,
            "servers": servers,
            "active_channel": channel_id or "",
            "active_guild": guild_id or "",
            "user": user,
        },
    )


@app.get("/api/songs", response_class=HTMLResponse)
async def api_songs(
    request: Request,
    channel_id: str | None = None,
    guild_id: str | None = None,
    sort: str = "newest",
):
    """HTMX fragment: returns the latest songs as HTML."""
    user = _get_current_user(request)
    songs = await get_songs_newest_first(
        limit=50,
        channel_id=channel_id,
        current_user_id=user["id"] if user else None,
        sort_by="votes" if sort == "votes" else "newest",
        guild_id=guild_id,
    )
    if not songs:
        return '<p class="empty">No songs collected yet. Post a music link in Discord!</p>'
    return "\n".join(_render_song_card(song, is_logged_in=user is not None) for song in songs)


@app.post("/api/songs/{song_id}/vote")
async def api_vote_song(request: Request, song_id: str):
    """Toggle upvote for the authenticated Discord user on a song."""
    user = _get_current_user(request)
    if not user:
        return JSONResponse(
            {"error": "Discord login required to vote"},
            status_code=401,
        )
    result = await toggle_vote(
        song_id=song_id,
        user_id=str(user["id"]),
        username=user.get("username"),
        avatar_url=user.get("avatar_url"),
    )
    if request.headers.get("HX-Request"):
        return HTMLResponse(
            _render_vote_button(
                song_id=song_id,
                vote_count=result["vote_count"],
                voted=result["voted"],
                is_logged_in=True,
            )
        )
    return result


@app.get("/api/invite")
async def api_invite(guild_id: str | None = None):
    """Return the least-privilege Discord OAuth2 bot invite URL."""
    client_id = os.environ.get("DISCORD_CLIENT_ID", "")
    if not client_id:
        return JSONResponse(
            {"error": "DISCORD_CLIENT_ID is not configured"},
            status_code=503,
        )
    return {
        "invite_url": build_bot_invite_url(client_id, guild_id=guild_id),
        "guild_id": guild_id,
    }


@app.get("/invite")
async def invite_redirect(guild_id: str | None = None):
    """Redirect directly to the Discord OAuth2 bot authorization screen."""
    client_id = os.environ.get("DISCORD_CLIENT_ID", "")
    if not client_id:
        return JSONResponse(
            {"error": "DISCORD_CLIENT_ID is not configured"},
            status_code=503,
        )
    return RedirectResponse(build_bot_invite_url(client_id, guild_id=guild_id), status_code=302)


@app.get("/api/servers")
async def api_servers(request: Request):
    """Return known Discord servers and management permissions for the current user."""
    user = _get_current_user(request)
    return {"servers": await list_servers(user=user)}


@app.get("/api/servers/{guild_id}/config")
async def api_get_server_config(request: Request, guild_id: str):
    """Return the configuration profile and known channels for a Discord server."""
    if not is_valid_id(guild_id):
        return JSONResponse({"error": "Invalid guild_id"}, status_code=400)
    user = _get_current_user(request)
    profile = await get_server_profile(guild_id)
    channels = await get_guild_channels(guild_id)
    can_manage = await can_user_manage_guild(user, guild_id)
    return {
        **profile,
        "available_channels": channels,
        "can_manage": can_manage,
    }


@app.put("/api/servers/{guild_id}/config")
async def api_update_server_config(request: Request, guild_id: str):
    """Update a Discord server's configuration profile (requires MANAGE_GUILD)."""
    user = _get_current_user(request)
    if not user:
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    if not await can_user_manage_guild(user, guild_id):
        return JSONResponse(
            {"error": "Manage Server permission required for this Discord server"},
            status_code=403,
        )
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    clean, err = validate_server_config_payload(guild_id, payload)
    if err or clean is None:
        return JSONResponse({"error": err or "Invalid configuration"}, status_code=400)

    updated = await upsert_server_profile(guild_id=guild_id, **clean)
    return updated


@app.get("/auth/discord")
async def auth_discord(request: Request):
    """Redirect the user to Discord OAuth2 authorization screen."""
    client_id = os.environ.get("DISCORD_CLIENT_ID", "")
    redirect_uri = os.environ.get(
        "DISCORD_REDIRECT_URI",
        str(request.url_for("auth_discord_callback")),
    )
    if not client_id:
        return JSONResponse(
            {"error": "DISCORD_CLIENT_ID is not configured"},
            status_code=503,
        )
    state_val = secrets.token_urlsafe(24)  # CSRF state nonce
    params = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "identify guilds",
            "state": state_val,
        }
    )
    resp = RedirectResponse(f"https://discord.com/api/oauth2/authorize?{params}", status_code=302)
    resp.set_cookie(STATE_COOKIE, state_val, httponly=True, max_age=600, samesite="lax")
    return resp


async def _fetch_discord_guilds(client: httpx.AsyncClient, bearer: str, uid: str) -> None:
    """Fetch and store the authenticated Discord user's guild memberships if available."""
    try:
        g_resp = await client.get(
            "https://discord.com/api/users/@me/guilds",
            headers={"Authorization": f"Bearer {bearer}"},
        )
        if g_resp.status_code == 200 and isinstance(g_resp.json(), list):
            await save_user_guilds(uid, g_resp.json())
    except Exception:
        return


async def _exchange_discord_code(code: str, redirect_uri: str) -> dict[str, Any] | None:
    """Exchange OAuth2 code for Discord user profile via Discord API."""
    client_id = os.environ.get("DISCORD_CLIENT_ID", "")
    client_sec = os.environ.get("DISCORD_CLIENT_SECRET", "")
    if not client_id or not client_sec:
        return None
    async with httpx.AsyncClient(timeout=10.0) as client:
        tok_resp = await client.post(
            "https://discord.com/api/oauth2/token",  # Discord OAuth2 endpoint
            data={
                "client_id": client_id,
                "client_secret": client_sec,  # Discord OAuth2 secret from env
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if tok_resp.status_code != 200:
            return None
        access_val = tok_resp.json().get("access_token")  # OAuth2 bearer from Discord
        if not access_val:
            return None
        me_resp = await client.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"Bearer {access_val}"},
        )
        if me_resp.status_code != 200:
            return None
        profile = me_resp.json()
        uid = str(profile.get("id", ""))
        await _fetch_discord_guilds(client, access_val, uid)
        avatar_hash = profile.get("avatar")
        avatar_url = (
            f"https://cdn.discordapp.com/avatars/{uid}/{avatar_hash}.png?size=64"
            if uid and avatar_hash
            else ""
        )
        return {
            "id": uid,
            "username": profile.get("global_name") or profile.get("username") or "Discord User",
            "avatar_url": avatar_url,
        }


@app.get("/auth/discord/callback")
async def auth_discord_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
):
    """Handle Discord OAuth2 callback, set signed session cookie, and redirect home."""
    expected_state = request.cookies.get(STATE_COOKIE)
    if not code or not state or not expected_state or state != expected_state:
        return JSONResponse({"error": "Invalid OAuth2 state or missing code"}, status_code=400)
    redirect_uri = os.environ.get(
        "DISCORD_REDIRECT_URI",
        str(request.url_for("auth_discord_callback")),
    )
    user = await _exchange_discord_code(code, redirect_uri)
    if not user:
        return JSONResponse({"error": "Discord OAuth2 exchange failed"}, status_code=502)
    resp = RedirectResponse("/", status_code=302)
    resp.delete_cookie(STATE_COOKIE)
    resp.set_cookie(
        SESSION_COOKIE,
        _encode_session(user),
        httponly=True,
        max_age=60 * 60 * 24 * 14,
        samesite="lax",
    )
    return resp


@app.get("/auth/logout")
@app.post("/auth/logout")
async def auth_logout():
    """Clear the Discord session cookie and redirect to the dashboard."""
    resp = RedirectResponse("/", status_code=302)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.get("/auth/me")
async def auth_me(request: Request):
    """Return the currently authenticated Discord user, if any."""
    user = _get_current_user(request)
    return {"authenticated": user is not None, "user": user}


@app.get("/api/export.m3u")
async def api_export_m3u(
    channel_id: str | None = None,
    guild_id: str | None = None,
):
    """Download the collected songs as an Extended M3U (.m3u) playlist file."""
    songs = await get_songs_newest_first(
        limit=500,
        channel_id=channel_id,
        guild_id=guild_id,
    )
    prefix = "Discord Playlist"
    if guild_id:
        prof = await get_server_profile(guild_id)
        prefix = prof.get("playlist_prefix") or prefix
    name = f"{prefix} #{channel_id}" if channel_id else prefix
    m3u_content = generate_m3u(songs, title=name)
    return Response(
        content=m3u_content,
        media_type="audio/x-mpegurl",
        headers={"Content-Disposition": 'attachment; filename="playlist.m3u"'},
    )


@app.get("/api/stats")
async def api_stats(
    channel_id: str | None = None,
    guild_id: str | None = None,
):
    """Return song, link, platform, and contributor statistics as JSON."""
    return await get_stats(channel_id=channel_id, guild_id=guild_id)


@app.get("/api/channels")
async def api_channels(guild_id: str | None = None):
    """Return distinct channel IDs with songs or playlists."""
    return {"channels": await get_channels(guild_id=guild_id)}


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

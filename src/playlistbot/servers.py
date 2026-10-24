"""Per-server profile management, in-memory TTL cache, and authorization."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from .db import get_db
from .models import Platform

# View Channels (1024) | Send Messages (2048) | Embed Links (16384)
# | Read Message History (65536) | Add Reactions (64)
# | Send Messages in Threads (274877906944)
BOT_INVITE_PERMISSIONS = 274877990976
_PERM_ADMIN = 0x8
_PERM_MANAGE_GUILD = 0x20
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_VALID_PLATFORMS = {p.value for p in Platform if p.value != "unknown"} | {
    "amazon_music",
    "qobuz",
    "napster",
    "pandora",
}

_CACHE_TTL_SEC = 2.0
_PROFILE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


def is_valid_id(val: str) -> bool:
    """Return True if val is a safe Discord snowflake or identifier."""
    return bool(_ID_RE.match(val))


def build_bot_invite_url(
    client_id: str,
    guild_id: str | None = None,
    permissions: int = BOT_INVITE_PERMISSIONS,
) -> str:
    """Build a least-privilege Discord OAuth2 bot + slash-commands invite URL."""
    params: dict[str, str] = {
        "client_id": client_id,
        "permissions": str(permissions),
        "scope": "bot applications.commands",
    }
    if guild_id and is_valid_id(guild_id):
        params["guild_id"] = guild_id
        params["disable_guild_select"] = "true"
    return f"https://discord.com/oauth2/authorize?{urlencode(params)}"


def invalidate_server_cache(guild_id: str | None = None) -> None:
    """Invalidate one server profile or the entire in-memory profile cache."""
    if guild_id is None:
        _PROFILE_CACHE.clear()
    else:
        _PROFILE_CACHE.pop(guild_id, None)


def _default_profile(guild_id: str) -> dict[str, Any]:
    return {
        "guild_id": guild_id,
        "name": f"Server {guild_id}",
        "icon_url": "",
        "allowed_channels": [],
        "enabled_platforms": [],
        "auto_sync_playlists": True,
        "thread_card_enabled": True,
        "playlist_prefix": "Discord Playlist",
        "playlist_urls": {},
        "updated_at": "",
    }


def _row_to_profile(row: Any) -> dict[str, Any]:
    raw_urls = row["playlist_urls"] if "playlist_urls" in row.keys() else "{}"
    return {
        "guild_id": row["guild_id"],
        "name": row["name"],
        "icon_url": row["icon_url"] or "",
        "allowed_channels": json.loads(row["allowed_channels"] or "[]"),
        "enabled_platforms": json.loads(row["enabled_platforms"] or "[]"),
        "auto_sync_playlists": bool(row["auto_sync_playlists"]),
        "thread_card_enabled": bool(row["thread_card_enabled"]),
        "playlist_prefix": row["playlist_prefix"] or "Discord Playlist",
        "playlist_urls": json.loads(raw_urls or "{}"),
        "updated_at": row["updated_at"] or "",
    }


async def get_server_profile(guild_id: str) -> dict[str, Any]:
    """Fetch a server profile from SQLite, or return defaults if unconfigured."""
    async with get_db() as db:
        rows = await db.execute_fetchall(
            "SELECT * FROM servers WHERE guild_id = ?",
            (guild_id,),
        )
        if not rows:
            return _default_profile(guild_id)
        return _row_to_profile(rows[0])


async def get_server_profile_cached(guild_id: str) -> dict[str, Any]:
    """Return a server profile from the in-memory TTL cache or SQLite."""
    now = time.monotonic()
    cached = _PROFILE_CACHE.get(guild_id)
    if cached is not None and (now - cached[0]) < _CACHE_TTL_SEC:
        return dict(cached[1])
    profile = await get_server_profile(guild_id)
    _PROFILE_CACHE[guild_id] = (now, profile)
    return dict(profile)


async def upsert_server_profile(
    guild_id: str,
    name: str | None = None,
    icon_url: str | None = None,
    allowed_channels: list[str] | None = None,
    enabled_platforms: list[str] | None = None,
    auto_sync_playlists: bool | None = None,
    thread_card_enabled: bool | None = None,
    playlist_prefix: str | None = None,
    playlist_urls: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create or update a server profile and immediately refresh the in-memory cache."""
    current = await get_server_profile(guild_id)
    merged = {
        "guild_id": guild_id,
        "name": (name.strip() if name else current["name"]) or f"Server {guild_id}",
        "icon_url": icon_url if icon_url is not None else current["icon_url"],
        "allowed_channels": (
            allowed_channels if allowed_channels is not None else current["allowed_channels"]
        ),
        "enabled_platforms": (
            enabled_platforms if enabled_platforms is not None else current["enabled_platforms"]
        ),
        "auto_sync_playlists": (
            auto_sync_playlists
            if auto_sync_playlists is not None
            else current["auto_sync_playlists"]
        ),
        "thread_card_enabled": (
            thread_card_enabled
            if thread_card_enabled is not None
            else current["thread_card_enabled"]
        ),
        "playlist_prefix": (
            playlist_prefix.strip()
            if playlist_prefix is not None and playlist_prefix.strip()
            else current["playlist_prefix"]
        ),
        "playlist_urls": (
            dict(playlist_urls)
            if playlist_urls is not None
            else dict(current.get("playlist_urls") or {})
        ),
        "updated_at": _now(),
    }
    await _persist_profile(merged)
    _PROFILE_CACHE[guild_id] = (time.monotonic(), merged)
    return dict(merged)


async def _persist_profile(merged: dict[str, Any]) -> None:
    async with get_db() as db:
        await db.execute(
            """INSERT INTO servers (
                   guild_id, name, icon_url, allowed_channels, enabled_platforms,
                   auto_sync_playlists, thread_card_enabled, playlist_prefix,
                   playlist_urls, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(guild_id) DO UPDATE SET
                   name = excluded.name,
                   icon_url = excluded.icon_url,
                   allowed_channels = excluded.allowed_channels,
                   enabled_platforms = excluded.enabled_platforms,
                   auto_sync_playlists = excluded.auto_sync_playlists,
                   thread_card_enabled = excluded.thread_card_enabled,
                   playlist_prefix = excluded.playlist_prefix,
                   playlist_urls = excluded.playlist_urls,
                   updated_at = excluded.updated_at""",
            (
                merged["guild_id"],
                merged["name"],
                merged["icon_url"],
                json.dumps(merged["allowed_channels"]),
                json.dumps(merged["enabled_platforms"]),
                1 if merged["auto_sync_playlists"] else 0,
                1 if merged["thread_card_enabled"] else 0,
                merged["playlist_prefix"],
                json.dumps(merged.get("playlist_urls") or {}),
                merged["updated_at"],
            ),
        )
        await db.commit()


async def sync_guild_channels(guild_id: str, channels: list[dict[str, str]]) -> None:
    """Replace stored text channel directory for a Discord guild."""
    now = _now()
    async with get_db() as db:
        await db.execute("DELETE FROM guild_channels WHERE guild_id = ?", (guild_id,))
        for ch in channels:
            cid = str(ch.get("id", "")).strip()
            cname = str(ch.get("name", "")).strip()
            if cid and cname:
                await db.execute(
                    "INSERT INTO guild_channels (channel_id, guild_id, name, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (cid, guild_id, cname, now),
                )
        await db.commit()


async def get_guild_channels(guild_id: str) -> list[dict[str, str]]:
    """Return known text channels for a guild, ordered by name."""
    async with get_db() as db:
        rows = await db.execute_fetchall(
            "SELECT channel_id, name FROM guild_channels WHERE guild_id = ? ORDER BY name",
            (guild_id,),
        )
        return [{"id": r["channel_id"], "name": r["name"]} for r in rows]


def _has_manage_permission(guild_data: dict[str, Any]) -> bool:
    if guild_data.get("owner") is True:
        return True
    try:
        perms = int(guild_data.get("permissions") or 0)
    except (TypeError, ValueError):
        perms = 0
    return bool(perms & (_PERM_ADMIN | _PERM_MANAGE_GUILD))


async def save_user_guilds(user_id: str, guilds: list[dict[str, Any]]) -> None:
    """Store a Discord user's guild memberships and management permissions."""
    now = _now()
    async with get_db() as db:
        await db.execute("DELETE FROM user_guilds WHERE user_id = ?", (user_id,))
        for g in guilds:
            gid = str(g.get("id", "")).strip()
            name = str(g.get("name", "")).strip() or f"Server {gid}"
            if not gid:
                continue
            icon_hash = g.get("icon")
            icon_url = (
                f"https://cdn.discordapp.com/icons/{gid}/{icon_hash}.png?size=64"
                if icon_hash
                else ""
            )
            can_manage = 1 if _has_manage_permission(g) else 0
            await db.execute(
                """INSERT INTO user_guilds
                   (user_id, guild_id, guild_name, guild_icon, can_manage, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, gid, name, icon_url, can_manage, now),
            )
        await db.commit()


def _bot_admin_ids() -> set[str]:
    return {x.strip() for x in os.environ.get("BOT_ADMIN_IDS", "").split(",") if x.strip()}


def _is_global_admin(user: dict[str, Any] | None) -> bool:
    if not user or not user.get("id"):
        return False
    if user.get("is_admin") is True:
        return True
    return str(user["id"]) in _bot_admin_ids()


async def get_user_guild_ids(user: dict[str, Any] | None) -> set[str] | None:
    """Return the set of guild IDs the user belongs to, or None if user is a global bot admin."""
    if not user or not user.get("id"):
        return set()
    if _is_global_admin(user):
        return None
    uid = str(user["id"])
    gids = set(user.get("guilds") or []) | set(user.get("manage_guilds") or [])
    async with get_db() as db:
        rows = await db.execute_fetchall(
            "SELECT guild_id FROM user_guilds WHERE user_id = ?",
            (uid,),
        )
        for r in rows:
            gids.add(str(r["guild_id"]))
    return gids


async def can_user_view_guild(user: dict[str, Any] | None, guild_id: str) -> bool:
    """Return True if the authenticated user is a member of the given guild (or bot admin)."""
    if not user or not user.get("id") or not guild_id:
        return False
    allowed = await get_user_guild_ids(user)
    return True if allowed is None else (guild_id in allowed)


async def can_user_manage_guild(user: dict[str, Any] | None, guild_id: str) -> bool:
    """Return True if the authenticated user can configure/edit the given guild."""
    if not user or not user.get("id"):
        return False
    if _is_global_admin(user):
        return True
    uid = str(user["id"])
    if not guild_id:
        return False
    if guild_id in (user.get("manage_guilds") or []):
        return True
    async with get_db() as db:
        rows = await db.execute_fetchall(
            "SELECT can_manage FROM user_guilds WHERE user_id = ? AND guild_id = ?",
            (uid, guild_id),
        )
        return bool(rows and rows[0]["can_manage"])


def _merge_server_rows(
    srv_rows: Any,
    song_guilds: Any,
    ug_rows: Any,
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in srv_rows:
        prof = _row_to_profile(row)
        by_id[prof["guild_id"]] = {**prof, "can_manage": False, "bot_installed": True}
    for row in song_guilds:
        gid = row["guild_id"]
        if gid not in by_id:
            by_id[gid] = {**_default_profile(gid), "can_manage": False, "bot_installed": True}
    member_gids: set[str] = set()
    for row in ug_rows:
        gid = row["guild_id"]
        member_gids.add(gid)
        entry = by_id.get(gid) or {
            **_default_profile(gid),
            "name": row["guild_name"],
            "icon_url": row["guild_icon"] or "",
            "bot_installed": False,
        }
        if row["guild_icon"] and not entry.get("icon_url"):
            entry["icon_url"] = row["guild_icon"]
        entry["can_manage"] = bool(row["can_manage"])
        by_id[gid] = entry
    return by_id, member_gids


async def list_servers(user: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return servers visible to the user (or all servers when user=None for internal queries)."""
    uid = str(user["id"]) if user and user.get("id") else None
    async with get_db() as db:
        srv_rows = await db.execute_fetchall("SELECT * FROM servers ORDER BY name")
        song_guilds = await db.execute_fetchall(
            "SELECT DISTINCT guild_id FROM songs WHERE guild_id IS NOT NULL AND guild_id != ''"
        )
        ug_rows = (
            await db.execute_fetchall(
                "SELECT * FROM user_guilds WHERE user_id = ? ORDER BY guild_name",
                (uid,),
            )
            if uid
            else []
        )

    by_id, member_gids = _merge_server_rows(srv_rows, song_guilds, ug_rows)
    if user and uid:
        is_bot_admin = _is_global_admin(user)
        explicit_manage = set(user.get("manage_guilds") or [])
        member_gids |= explicit_manage | set(user.get("guilds") or [])
        for gid, entry in by_id.items():
            if not entry.get("can_manage") and (is_bot_admin or gid in explicit_manage):
                entry["can_manage"] = True
        if not is_bot_admin:
            return [v for k, v in by_id.items() if k in member_gids]
    return list(by_id.values())


def _clean_channel_list(chans: Any) -> tuple[list[str] | None, str | None]:
    if not isinstance(chans, list) or len(chans) > 50:
        return None, "allowed_channels must be a list of at most 50 channel IDs"
    cleaned: list[str] = []
    for ch in chans:
        s = str(ch).strip()
        if not s:
            continue
        if not is_valid_id(s):
            return None, f"Invalid channel ID: {s}"
        if s not in cleaned:
            cleaned.append(s)
    return cleaned, None


def _clean_platform_list(plats: Any) -> tuple[list[str] | None, str | None]:
    if not isinstance(plats, list):
        return None, "enabled_platforms must be a list"
    cleaned: list[str] = []
    for p in plats:
        s = str(p).strip().lower()
        if s not in _VALID_PLATFORMS and s != "soundcloud":
            return None, f"Unsupported platform: {s}"
        if s not in cleaned:
            cleaned.append(s)
    return cleaned, None


def _clean_playlist_urls(urls_map: Any) -> tuple[dict[str, str] | None, str | None]:
    if not isinstance(urls_map, dict):
        return None, "playlist_urls must be a JSON object"
    cleaned: dict[str, str] = {}
    for k, v in urls_map.items():
        plat = str(k).strip().lower()
        if plat == "sound_cloud":
            plat = "soundcloud"
        if plat not in _VALID_PLATFORMS and plat != "soundcloud":
            return None, f"Unsupported playlist platform: {plat}"
        val = str(v or "").strip()
        if not val:
            continue
        if not val.startswith("https://") or len(val) > 512:
            return None, f"Invalid playlist URL for {plat} (must start with https://)"
        cleaned[plat] = val
    return cleaned, None


def validate_server_config_payload(
    guild_id: str,
    payload: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Validate and sanitize a server configuration update payload."""
    if not is_valid_id(guild_id):
        return None, "Invalid guild_id"
    if not isinstance(payload, dict):
        return None, "Invalid JSON object"

    clean: dict[str, Any] = {}
    if "name" in payload and payload["name"] is not None:
        name = re.sub(r"[\r\n\t]", " ", str(payload["name"])).strip()
        if not (1 <= len(name) <= 80):
            return None, "Server name must be between 1 and 80 characters"
        clean["name"] = name

    if "allowed_channels" in payload and payload["allowed_channels"] is not None:
        cleaned_chans, err = _clean_channel_list(payload["allowed_channels"])
        if err:
            return None, err
        clean["allowed_channels"] = cleaned_chans

    if "enabled_platforms" in payload and payload["enabled_platforms"] is not None:
        cleaned_plats, err = _clean_platform_list(payload["enabled_platforms"])
        if err:
            return None, err
        clean["enabled_platforms"] = cleaned_plats

    if "playlist_urls" in payload and payload["playlist_urls"] is not None:
        cleaned_urls, err = _clean_playlist_urls(payload["playlist_urls"])
        if err:
            return None, err
        clean["playlist_urls"] = cleaned_urls

    if "auto_sync_playlists" in payload:
        clean["auto_sync_playlists"] = bool(payload["auto_sync_playlists"])
    if "thread_card_enabled" in payload:
        clean["thread_card_enabled"] = bool(payload["thread_card_enabled"])
    if "playlist_prefix" in payload and payload["playlist_prefix"] is not None:
        prefix = re.sub(r"[\r\n\t]", " ", str(payload["playlist_prefix"])).strip()
        if not (1 <= len(prefix) <= 80):
            return None, "playlist_prefix must be between 1 and 80 characters"
        clean["playlist_prefix"] = prefix

    return clean, None

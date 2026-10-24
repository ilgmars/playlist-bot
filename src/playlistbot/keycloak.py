"""Keycloak OIDC authentication helper for custom realm (`playlist-bot`)."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlencode

import httpx


def _issuer_url() -> str:
    raw = os.environ.get("KEYCLOAK_ISSUER_URL", "").strip().rstrip("/")
    if raw:
        return raw
    base = os.environ.get("KEYCLOAK_BASE_URL", "").strip().rstrip("/")
    realm = os.environ.get("KEYCLOAK_REALM", "playlist-bot").strip() or "playlist-bot"
    if base:
        return f"{base}/realms/{realm}"
    return ""


def _internal_issuer_url() -> str:
    """Return internal container-to-container Keycloak issuer URL when set, else public issuer."""
    internal = os.environ.get("KEYCLOAK_INTERNAL_ISSUER_URL", "").strip().rstrip("/")
    return internal or _issuer_url()


def is_keycloak_configured() -> bool:
    """Return True if Keycloak OIDC issuer and client ID are configured."""
    return bool(_issuer_url() and os.environ.get("KEYCLOAK_CLIENT_ID", "").strip())


def build_keycloak_auth_url(redirect_uri: str, state: str) -> str:
    """Build the Keycloak OIDC authorization endpoint URL."""
    issuer = _issuer_url()
    client_id = os.environ.get("KEYCLOAK_CLIENT_ID", "").strip()
    params = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid profile email",
            "state": state,
        }
    )
    return f"{issuer}/protocol/openid-connect/auth?{params}"


def _extract_roles(claims: dict[str, Any]) -> list[str]:
    roles: list[str] = []
    raw_roles = claims.get("roles")
    if isinstance(raw_roles, list):
        roles.extend(str(r) for r in raw_roles if r)
    realm_access = claims.get("realm_access")
    if isinstance(realm_access, dict) and isinstance(realm_access.get("roles"), list):
        roles.extend(str(r) for r in realm_access["roles"] if r)
    client_id = os.environ.get("KEYCLOAK_CLIENT_ID", "playlist-bot-web").strip()
    res_access = claims.get("resource_access")
    if isinstance(res_access, dict):
        client_obj = res_access.get(client_id)
        if isinstance(client_obj, dict) and isinstance(client_obj.get("roles"), list):
            roles.extend(str(r) for r in client_obj["roles"] if r)
    return sorted(set(roles))


def _extract_guild_list(val: Any) -> list[str]:
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str):
        return [x.strip() for x in val.split(",") if x.strip()]
    return []


def map_keycloak_profile(claims: dict[str, Any]) -> dict[str, Any] | None:
    """Map Keycloak OIDC userinfo claims and custom realm roles to a session user dict."""
    uid = str(claims.get("discord_user_id") or claims.get("sub") or "").strip()
    if not uid:
        return None
    roles = _extract_roles(claims)
    admin_role = os.environ.get("KEYCLOAK_ADMIN_ROLE", "playlist-admin").strip() or "playlist-admin"
    is_admin = admin_role in roles or "admin" in roles or "playlist-admin" in roles
    guilds = set(_extract_guild_list(claims.get("guilds")))
    manage_guilds = set(_extract_guild_list(claims.get("manage_guilds")))
    for role in roles:
        if role.startswith("server-admin:"):
            gid = role.split(":", 1)[1].strip()
            if gid:
                manage_guilds.add(gid)
                guilds.add(gid)
        elif role.startswith("server-member:"):
            gid = role.split(":", 1)[1].strip()
            if gid:
                guilds.add(gid)
    username = (
        claims.get("name")
        or claims.get("preferred_username")
        or claims.get("email")
        or "Keycloak User"
    )
    return {
        "id": uid,
        "username": str(username),
        "avatar_url": str(claims.get("picture") or claims.get("avatar_url") or ""),
        "auth_provider": "keycloak",
        "is_admin": is_admin,
        "roles": roles,
        "guilds": sorted(guilds),
        "manage_guilds": sorted(manage_guilds),
    }


async def exchange_keycloak_code(code: str, redirect_uri: str) -> dict[str, Any] | None:
    """Exchange OAuth2 authorization code against Keycloak OIDC token + userinfo endpoints."""
    issuer = _internal_issuer_url()
    client_id = os.environ.get("KEYCLOAK_CLIENT_ID", "").strip()
    client_sec = os.environ.get("KEYCLOAK_CLIENT_SECRET", "").strip()
    if not issuer or not client_id:
        return None
    form_data: dict[str, str] = {
        "client_id": client_id,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    if client_sec:
        form_data["client_secret"] = client_sec
    async with httpx.AsyncClient(timeout=10.0) as client:
        tok_resp = await client.post(
            f"{issuer}/protocol/openid-connect/token",
            data=form_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if tok_resp.status_code != 200:
            return None
        bearer = tok_resp.json().get("access_token")
        if not bearer:
            return None
        info_resp = await client.get(
            f"{issuer}/protocol/openid-connect/userinfo",
            headers={"Authorization": f"Bearer {bearer}"},
        )
        if info_resp.status_code != 200:
            return None
        return map_keycloak_profile(info_resp.json())

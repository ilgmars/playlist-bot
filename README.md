# playlist-bot

Discord bot and FastAPI web dashboard that collects music links from Discord channels, resolves cross-platform equivalents, syncs playlists, and lets members sign in via Discord OAuth2 or Keycloak OIDC (`playlist-bot` realm) to view server playlists, vote for tracks, and edit playlists.

## Deploy

```sh
curl -fsSL https://raw.githubusercontent.com/ilgmars/playlist-bot/main/install.sh | bash
```

`install.sh` walks through configuration and writes `.env` before starting Docker Compose:
1. **Discord Bot & OAuth2** (`https://discord.com/developers/applications`: bot token, client ID/secret, OAuth2 redirect URI, and admin user IDs for `BOT_ADMIN_IDS`).
2. **Cloudflare Tunnel (`cloudflared`)** (`https://one.dash.cloudflare.com/`: tunnel token `CF_TUNNEL_TOKEN` and public hostname routing to `http://playlist-bot:8000`).
3. **Authentication & Keycloak realm (`playlist-bot`)**: direct Discord OAuth2, Keycloak OIDC (either a bundled `quay.io/keycloak/keycloak:26.0` container importing `deploy/keycloak/playlist-bot-realm.json` or an existing Keycloak instance), or both.
4. **Spotify & YouTube API credentials** (`https://developer.spotify.com/dashboard`, `https://console.cloud.google.com/apis/library/youtube.googleapis.com`).

## Development

```sh
make setup
make test
make dev
```

## Features

- **Link collection**: detects music links across 12 services (YouTube, YouTube Music, Spotify, Apple Music, SoundCloud, Bandcamp, Deezer, Tidal, Amazon Music, Qobuz, Napster, Pandora).
- **Cross-platform matching**: resolves shared links via Odesli (`song.link`) with rate-limit backoff and Deezer/yt-dlp oEmbed fallbacks.
- **Multi-platform playlists**: open synced playlists or multi-track YouTube/YT Music queues (`/playlists/{platform}`, `GET /api/playlists`), copy Spotify `spotify:track:...` URIs or track links (`GET /api/playlists/{platform}/tracks.txt`), and export `.m3u` files (`GET /api/export.m3u?platform=spotify`).
- **Server-scoped access & playlist editing**: users only see servers and playlists from Discord servers they belong to (`can_user_view_guild`). Server admins (`MANAGE_GUILD`, `ADMINISTRATOR`, `BOT_ADMIN_IDS`, or Keycloak `playlist-admin` / `server-admin:<guild_id>` roles) can update playlist URLs (`PUT /api/servers/{guild_id}/playlists`), add tracks (`POST /api/servers/{guild_id}/songs`), edit metadata (`PATCH /api/songs/{song_id}`), and delete tracks (`DELETE /api/songs/{song_id}`).
- **Keycloak OIDC (`deploy/keycloak/playlist-bot-realm.json`)**: `playlist-bot` realm export with `playlist-admin` and `playlist-member` roles, `playlist-bot-web` OIDC client, and Discord identity provider brokering (`/auth/keycloak`, `/auth/keycloak/callback`).
- **Slash commands**: `/playlist-create`, `/playlist-export`, `/playlist-links`, and `/playlist-stats`.
- **Per-server profiles**: per-guild settings (`allowed_channels`, `enabled_platforms`, `auto_sync_playlists`, `thread_card_enabled`, `playlist_prefix`, `playlist_urls`) cached in memory (`get_server_profile_cached`) and configurable from the web dashboard (`GET|PUT /api/servers/{guild_id}/config`).
- **Discord OAuth2 & voting**: sign in via `/auth/discord`, upvote tracks (`POST /api/songs/{song_id}/vote`), and sort by newest or top voted.
- **Storage**: SQLite in WAL mode (`songs`, `bot_cards`, `playlists`, `votes`, `servers`, `guild_channels`, `user_guilds`).

## Environment variables

| Variable | Description |
| --- | --- |
| `DISCORD_TOKEN` | Discord bot token |
| `DISCORD_CHANNEL_IDS` | Comma-separated fallback channel IDs when a guild has no custom profile |
| `DISCORD_CLIENT_ID` | Discord application client ID (OAuth2 login and `/invite` URL) |
| `DISCORD_CLIENT_SECRET` | Discord OAuth2 client secret |
| `DISCORD_REDIRECT_URI` | Optional callback URL (defaults to `<request-origin>/auth/discord/callback`) |
| `BOT_ADMIN_IDS` | Comma-separated Discord user IDs allowed to manage any guild profile |
| `CF_TUNNEL_TOKEN` | Cloudflare Tunnel token (`docker compose --profile cloudflared up -d`) |
| `KEYCLOAK_REALM` | Keycloak realm name (default: `playlist-bot`) |
| `KEYCLOAK_ISSUER_URL` | Public Keycloak OIDC realm issuer URL (e.g. `https://sso.example.com/realms/playlist-bot`) |
| `KEYCLOAK_INTERNAL_ISSUER_URL` | Optional internal issuer URL (e.g. `http://keycloak:8080/realms/playlist-bot`) |
| `KEYCLOAK_CLIENT_ID` | Keycloak OIDC client ID (default: `playlist-bot-web`) |
| `KEYCLOAK_CLIENT_SECRET` | Keycloak OIDC client secret |
| `KEYCLOAK_ADMIN_ROLE` | Keycloak role granting global playlist admin access (default: `playlist-admin`) |
| `SESSION_SECRET` | HMAC secret for signing session cookies |
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | Optional Spotify API credentials |
| `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` / `YOUTUBE_REFRESH_TOKEN` | Optional YouTube Data API v3 OAuth credentials |
| `DB_PATH` | SQLite database path (default: `playlist.db`) |

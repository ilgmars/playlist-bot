# playlist-bot

Discord bot and FastAPI web dashboard that collects music links from Discord channels, resolves cross-platform equivalents, syncs playlists, and lets members sign in with Discord to vote for tracks.

## Quick start

```sh
make setup     # install deps in a venv
make test      # run the full pytest suite
make dev       # run bot + web server in watch mode
```

## Features

- **Link collection** — detects music links across 12 services (YouTube, YouTube Music, Spotify, Apple Music, SoundCloud, Bandcamp, Deezer, Tidal, Amazon Music, Qobuz, Napster, Pandora).
- **Cross-platform matching** — resolves shared links via Odesli (`song.link`) with rate-limit backoff and Deezer/yt-dlp oEmbed fallbacks.
- **Multi-platform playlists (Spotify, YouTube, YT Music, Apple Music, Deezer, SoundCloud, Tidal)** — open live synced playlists or instant multi-track YouTube/YT Music queues (`/playlists/{platform}`, `GET /api/playlists`), copy Spotify `spotify:track:...` URIs / platform links (`GET /api/playlists/{platform}/tracks.txt`), and export platform-specific `.m3u` files (`GET /api/export.m3u?platform=spotify`).
- **Slash commands** — `/playlist-create`, `/playlist-export`, `/playlist-links`, and `/playlist-stats`.
- **Multi-server profiles & zero-latency cache** — per-guild profiles (`allowed_channels`, `enabled_platforms`, `auto_sync_playlists`, `thread_card_enabled`, `playlist_prefix`) backed by an O(1) in-memory monotonic TTL cache (`get_server_profile_cached`) with immediate write-through invalidation.
- **Web server configuration & bot invite** — configure server channels, platforms, and sync behavior directly from the slide-over dashboard drawer (`GET|PUT /api/servers/{guild_id}/config`), gated by Discord `MANAGE_GUILD` / `ADMINISTRATOR` / guild owner permissions or `BOT_ADMIN_IDS`, plus 1-click bot invite (`GET /api/invite`, `GET /invite`).
- **Multi-channel & server filtering** — filter the dashboard, stats, channels, and `.m3u` export by `guild_id` and `channel_id`.
- **Discord OAuth2 & song voting** — sign in via `/auth/discord` (`identify guilds` scope), upvote tracks (`POST /api/songs/{song_id}/vote`), and sort the dashboard by **Newest** or **Top Voted**.
- **Thread-safe storage** — SQLite in WAL mode (`songs`, `bot_cards`, `playlists`, `votes`, `servers`, `guild_channels`, `user_guilds`).

## Environment variables

| Variable | Description |
| --- | --- |
| `DISCORD_TOKEN` | Discord bot token |
| `DISCORD_CHANNEL_IDS` | Comma-separated fallback channel IDs to watch when a guild has no custom profile |
| `DISCORD_CLIENT_ID` | Discord application/bot client ID (used for OAuth2 login and `/invite` URL generation) |
| `DISCORD_CLIENT_SECRET` | Discord OAuth2 application client secret |
| `DISCORD_REDIRECT_URI` | Optional explicit callback URL (defaults to `<request-origin>/auth/discord/callback`) |
| `BOT_ADMIN_IDS` | Optional comma-separated Discord user IDs allowed to configure any guild profile |
| `SESSION_SECRET` | HMAC secret for signing session cookies |
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | Optional Spotify API credentials |
| `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` / `YOUTUBE_REFRESH_TOKEN` | Optional YouTube Data API v3 OAuth credentials |
| `DB_PATH` | SQLite database path (default: `playlist.db`) |

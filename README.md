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
- **Playlist sync & export** — appends matched tracks to YouTube and Spotify playlists and exports `.m3u` files via `/api/export.m3u` or `/playlist-export`.
- **Slash commands** — `/playlist-create`, `/playlist-export`, `/playlist-links`, and `/playlist-stats`.
- **Multi-channel filtering** — monitors comma-separated `DISCORD_CHANNEL_IDS` (or `DISCORD_CHANNEL_ID`) and filters the dashboard by channel.
- **Discord OAuth2 & song voting** — sign in via `/auth/discord`, upvote tracks (`POST /api/songs/{song_id}/vote`), and sort the dashboard by **Newest** or **Top Voted**.
- **Thread-safe storage** — SQLite in WAL mode (`songs`, `bot_cards`, `playlists`, `votes`).

## Environment variables

| Variable | Description |
| --- | --- |
| `DISCORD_TOKEN` | Discord bot token |
| `DISCORD_CHANNEL_IDS` | Comma-separated channel IDs to watch (falls back to `DISCORD_CHANNEL_ID`) |
| `DISCORD_CLIENT_ID` | Discord OAuth2 application client ID for dashboard login |
| `DISCORD_CLIENT_SECRET` | Discord OAuth2 application client secret |
| `DISCORD_REDIRECT_URI` | Optional explicit callback URL (defaults to `<request-origin>/auth/discord/callback`) |
| `SESSION_SECRET` | HMAC secret for signing session cookies |
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | Optional Spotify API credentials |
| `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` / `YOUTUBE_REFRESH_TOKEN` | Optional YouTube Data API v3 OAuth credentials |
| `DB_PATH` | SQLite database path (default: `playlist.db`) |

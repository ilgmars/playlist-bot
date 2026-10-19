# playlist-bot

Discord bot that collects music links from channels and creates cross-platform playlists
(YouTube, Spotify, Apple Music, SoundCloud, Bandcamp). Web dashboard shows the latest
activity with a single card per bot message — no duplicates.

## Quick start

```sh
make setup     # install deps in a venv
make test      # run the full pytest suite
make dev      # run bot + web server in watch mode
```

## Features

- **Link collection** — listens to Discord channels, detects music links across 5+ platforms
- **Cross-platform playlists** — creates/updates playlists on YouTube and Spotify (Apple Music via MusicKit)
- **Thread card** — bot edits its own last message in place instead of spamming the channel
- **Web dashboard** — HTMX-powered FastAPI frontend showing recent songs and all links per song
- **Thread-safe** — SQLite WAL mode for concurrent Discord + web + worker access

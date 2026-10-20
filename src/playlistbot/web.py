"""Web dashboard — FastAPI backend with Jinja2 + HTMX template rendering."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from .db import get_songs_newest_first

app = FastAPI(title="Playlist Bot Dashboard", version="0.1.0")

_template_dir = Path(__file__).resolve().parent / "templates"
_static_dir = Path(__file__).resolve().parent / "static"

if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

templates = Jinja2Templates(directory=str(_template_dir))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Main dashboard page showing the latest songs, newest first."""
    songs = await get_songs_newest_first(limit=50)
    return templates.TemplateResponse(request, "index.html", {"songs": songs})


@app.get("/api/songs", response_class=HTMLResponse)
async def api_songs():
    """HTMX fragment: returns the latest songs as HTML."""
    songs = await get_songs_newest_first(limit=50)
    parts: list[str] = []
    for song in songs:
        link_tags = " ".join(
            f'<a href="{link["original_url"]}" target="_blank" rel="noopener">'
            f"{link['platform'].capitalize()}</a>"
            for link in song.get("links", [])
        )
        parts.append(
            f"""<article class="song-card">
  <div class="song-header">
    <strong class="song-title">{song["title"]}</strong>
    <span class="song-artist">{song["artist"]}</span>
    <time class="song-time">{song["created_at"]}</time>
  </div>
  <div class="song-links">{link_tags}</div>
</article>"""
        )
    if not songs:
        return '<p class="empty">No songs collected yet. Post a music link in Discord!</p>'
    return "\n".join(parts)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

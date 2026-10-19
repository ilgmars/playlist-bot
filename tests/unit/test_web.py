"""Tests for the web API endpoints."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from playlistbot.db import insert_song
from playlistbot.models import Platform, SongLink
from playlistbot.web import app


@pytest.fixture(autouse=True)
async def setup_db(tmp_path, monkeypatch):
    """Initialize an in-memory SQLite DB for each test."""
    import os

    import playlistbot.db as dbmod
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(dbmod, "DB_PATH", db_path)
    monkeypatch.setattr(dbmod, "DB_PATH", db_path)
    os.environ["DB_PATH"] = db_path
    # Also patch the module-level constant in any imported references
    await dbmod.init_db()
    yield
    if await asyncio.to_thread(os.path.exists, db_path):
        await asyncio.to_thread(os.remove, db_path)


@pytest.fixture
def client():
    return TestClient(app)


class TestHealthz:
    def test_healthz_returns_ok(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestIndexPage:
    def test_index_returns_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "<h1>Playlist Bot</h1>" in resp.text


class TestSongsApi:
    def test_songs_api_empty(self, client):
        resp = client.get("/api/songs")
        assert resp.status_code == 200
        assert "No songs" in resp.text

    @pytest.mark.asyncio
    async def test_songs_api_with_data(self, client):
        links = [SongLink(
            platform=Platform.YOUTUBE,
            original_url="https://youtube.com/watch?v=abc",
            canonical_url="https://youtube.com/watch?v=abc",
            track_id="abc123abc123",
        )]
        await insert_song(
            title="Test Song",
            artist="Test Artist",
            links=links,
        )
        resp = client.get("/api/songs")
        assert resp.status_code == 200
        assert "Test Song" in resp.text
        assert "Test Artist" in resp.text
        assert "youtube" in resp.text.lower()

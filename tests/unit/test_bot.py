"""Tests for the Discord bot thread-card state management.

The key requirement: the bot edits its own last message in a thread in place,
never creating duplicate messages.
"""
import asyncio

import pytest

from playlistbot.db import (
    get_thread_state,
    save_thread_state,
)


@pytest.fixture(autouse=True)
async def setup_db(tmp_path, monkeypatch):
    import os

    import playlistbot.db as dbmod
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(dbmod, "DB_PATH", db_path)
    os.environ["DB_PATH"] = db_path
    await dbmod.init_db()
    yield
    if await asyncio.to_thread(os.path.exists, db_path):
        await asyncio.to_thread(os.remove, db_path)


class TestThreadState:
    @pytest.mark.asyncio
    async def test_save_and_retrieve_thread_state(self):
        await save_thread_state(
            thread_id="123456",
            channel_id="789",
            last_message_id="999888",
            total_songs=1,
            latest_title="Test Song",
            latest_url="https://youtube.com/watch?v=abc",
        )
        state = await get_thread_state("123456")
        assert state is not None
        assert state["thread_id"] == "123456"
        assert state["channel_id"] == "789"
        assert state["last_message_id"] == "999888"
        assert state["total_songs"] == 1
        assert state["latest_song_title"] == "Test Song"
        assert state["latest_song_url"] == "https://youtube.com/watch?v=abc"

    @pytest.mark.asyncio
    async def test_update_thread_state_overwrites_message_id(self):
        """When the bot adds a second song, it updates the same message_id slot."""
        await save_thread_state(
            thread_id="123456",
            channel_id="789",
            last_message_id="msg111",
            total_songs=1,
        )
        await save_thread_state(
            thread_id="123456",
            channel_id="789",
            last_message_id="msg222",
            total_songs=2,
            latest_title="New Song",
        )
        state = await get_thread_state("123456")
        assert state["last_message_id"] == "msg222"
        assert state["total_songs"] == 2
        assert state["latest_song_title"] == "New Song"

    @pytest.mark.asyncio
    async def test_get_unknown_thread_returns_none(self):
        result = await get_thread_state("nonexistent-thread")
        assert result is None

    @pytest.mark.asyncio
    async def test_new_thread_has_no_last_message_id(self):
        """A fresh thread state should indicate the bot has never sent a message yet."""
        await save_thread_state(
            thread_id="111",
            channel_id="222",
            last_message_id=None,
            total_songs=0,
        )
        state = await get_thread_state("111")
        assert state["last_message_id"] is None
        assert state["total_songs"] == 0


class TestDedupLogic:
    @pytest.mark.asyncio
    async def test_known_link_returns_true(self):
        from playlistbot.db import insert_song, is_known_link
        from playlistbot.models import Platform, SongLink

        links = [
            SongLink(
                platform=Platform.YOUTUBE,
                original_url="https://youtube.com/watch?v=abc",
                canonical_url="https://youtube.com/watch?v=abc",
                track_id="abc123abc123",
            )
        ]
        await insert_song(title="Test", artist="Test", links=links)
        assert await is_known_link("https://youtube.com/watch?v=abc")

    @pytest.mark.asyncio
    async def test_unknown_link_returns_false(self):
        from playlistbot.db import is_known_link
        result = await is_known_link("https://youtube.com/watch?v=unknown123")
        assert not result

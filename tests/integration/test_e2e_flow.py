"""Integration test: full flow from link detection to thread card update.

This test exercises the real link parser + dedup logic without needing
actual Discord or YouTube/Spotify APIs.
"""

import asyncio

import pytest

from playlistbot.db import (
    get_songs_newest_first,
    get_thread_state,
    insert_song,
    is_known_link,
    save_thread_state,
)
from playlistbot.link_parser import Platform, extract_links
from playlistbot.models import SongLink


@pytest.fixture(autouse=True)
async def setup_db(tmp_path, monkeypatch):
    import os

    import playlistbot.db as dbmod

    db_path = str(tmp_path / "test_integration.db")
    monkeypatch.setattr(dbmod, "DB_PATH", db_path)
    os.environ["DB_PATH"] = db_path
    await dbmod.init_db()
    yield
    if await asyncio.to_thread(os.path.exists, db_path):
        await asyncio.to_thread(os.remove, db_path)


class TestEndToEndFlow:
    """Simulates the full pipeline: Discord message -> parse -> store -> dedup -> card state."""

    @pytest.mark.asyncio
    async def test_full_flow_no_duplicate(self):
        # Simulate a Discord message containing a YouTube link
        message_content = "Check out this track: https://youtu.be/dQw4w9WgXcQ"

        # Step 1: Extract links
        links = extract_links(message_content)
        assert len(links) == 1
        assert links[0].platform == Platform.YOUTUBE

        # Step 2: Check dedup - first time, not known
        assert not await is_known_link(links[0].canonical_url)

        # Step 3: Insert the song
        song_links = [
            SongLink(
                platform=links[0].platform,
                original_url=links[0].original_url,
                canonical_url=links[0].canonical_url,
                track_id=links[0].track_id,
                added_by="test_user",
                message_id="999",
            )
        ]
        await insert_song(
            title="Never Gonna Give You Up",
            artist="Rick Astley",
            links=song_links,
            added_by="test_user",
            message_id="999",
        )

        # Step 4: Now it IS known (dedup works)
        assert await is_known_link(links[0].canonical_url)

        # Step 5: Simulate another message with the SAME link - should be skipped
        _ = extract_links(message_content)
        already_known = await is_known_link(links[0].canonical_url)
        assert already_known  # bot would skip this

        # Step 6: Verify song is retrievable
        songs = await get_songs_newest_first(limit=10)
        assert len(songs) == 1
        assert songs[0]["title"] == "Never Gonna Give You Up"

    @pytest.mark.asyncio
    async def test_thread_card_updates_in_place(self):
        """The thread state tracks the single bot message that gets edited."""
        # Initial state: no thread, no songs
        assert await get_thread_state("thread-1") is None

        # After adding first song, save thread state with first bot message
        await save_thread_state(
            thread_id="thread-1",
            channel_id="chan-1",
            last_message_id="msg-1",
            total_songs=1,
            latest_title="Song A",
            latest_url="https://youtu.be/aaa",
        )

        state = await get_thread_state("thread-1")
        assert state["last_message_id"] == "msg-1"
        assert state["total_songs"] == 1

        # Second song arrives - bot edits msg-1 (same message_id), total increments
        await save_thread_state(
            thread_id="thread-1",
            channel_id="chan-1",
            last_message_id="msg-1",  # same message, edited in place
            total_songs=2,
            latest_title="Song B",
            latest_url="https://youtu.be/bbb",
        )

        state = await get_thread_state("thread-1")
        assert state["last_message_id"] == "msg-1"  # no new message created
        assert state["total_songs"] == 2
        assert state["latest_song_title"] == "Song B"

    @pytest.mark.asyncio
    async def test_multiple_different_platforms(self):
        """A single song can have links from multiple platforms."""
        message = (
            "New track! "
            "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQ3 "
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )
        links = extract_links(message)
        assert len(links) == 2
        assert Platform.SPOTIFY in [link.platform for link in links]
        assert Platform.YOUTUBE in [link.platform for link in links]

        # Both are new
        for link in links:
            assert not await is_known_link(link.canonical_url)

        # Store both in one song entry
        db_links = [
            SongLink(
                platform=link.platform,
                original_url=link.original_url,
                canonical_url=link.canonical_url,
                track_id=link.track_id,
            )
            for link in links
        ]
        await insert_song(
            title="Cross-platform Song",
            artist="Various",
            links=db_links,
        )

        songs = await get_songs_newest_first(limit=5)
        assert songs[0]["links"] == [
            {
                "platform": link.platform.value,
                "original_url": link.original_url,
                "canonical_url": link.canonical_url,
                "track_id": link.track_id,
                "added_by": None,
                "message_id": None,
            }
            for link in links
        ]

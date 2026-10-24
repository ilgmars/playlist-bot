"""Unit tests for playlistbot.bot — embeds, threads, DB lookups and run_bot."""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from playlistbot import bot as botmod
from playlistbot import db as dbmod
from playlistbot.link_parser import ParsedLink, Platform


@pytest.fixture(autouse=True)
async def setup_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "bot-test.db")
    monkeypatch.setattr(dbmod, "DB_PATH", db_path)
    monkeypatch.setenv("DB_PATH", db_path)
    await dbmod.init_db()
    yield
    if await asyncio.to_thread(os.path.exists, db_path):
        await asyncio.to_thread(os.remove, db_path)


def _link(
    platform: Platform = Platform.YOUTUBE,
    url: str = "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    track_id: str | None = "dQw4w9WgXcQ",
) -> ParsedLink:
    return ParsedLink(
        platform=platform,
        original_url=url,
        canonical_url=url,
        track_id=track_id,
    )


class _Author:
    def __init__(self, name: str, is_bot: bool = False) -> None:
        self.name = name
        self.bot = is_bot

    def __str__(self) -> str:
        return self.name


def _message(
    content: str = "",
    *,
    is_bot: bool = False,
    channel_id: int = 42,
    message_id: int = 777,
    author: str = "tester#0001",
) -> SimpleNamespace:
    return SimpleNamespace(
        content=content,
        id=message_id,
        author=_Author(author, is_bot),
        channel=SimpleNamespace(id=channel_id, threads=[], create_thread=AsyncMock()),
    )


class TestBuildCardEmbed:
    def test_embed_contains_track_and_links(self):
        embed = botmod._build_card_embed(
            song_title="Song",
            song_artist="Artist",
            all_links=[
                {"platform": "youtube", "original_url": "https://youtu.be/a"},
                {"platform": "spotify", "original_url": "https://open.spotify.com/track/b"},
            ],
            total=3,
        )
        assert isinstance(embed, discord.Embed)
        assert embed.title == "Latest song added"
        assert embed.color.value == 0x1DB954
        fields = {field.name: field.value for field in embed.fields}
        assert fields["Track"] == "**Song** — Artist"
        assert "[Youtube](https://youtu.be/a)" in fields["Links"]
        assert "[Spotify](https://open.spotify.com/track/b)" in fields["Links"]
        assert embed.footer.text == "3 songs collected"

    def test_embed_without_links_says_none_available(self):
        embed = botmod._build_card_embed("Song", "Artist", [], total=2)
        fields = {field.name: field.value for field in embed.fields}
        assert fields["Links"] == "No links available"
        assert embed.footer.text == "2 songs collected"


class TestGetOrCreateThread:
    async def test_reuses_existing_unarchived_track_thread(self):
        thread = SimpleNamespace(name="playlist-track", archived=False)
        channel = SimpleNamespace(
            threads=[SimpleNamespace(name="other", archived=False), thread],
            create_thread=AsyncMock(),
        )
        result = await botmod._get_or_create_thread(channel, "Song")
        assert result is thread
        channel.create_thread.assert_not_awaited()

    async def test_ignores_archived_thread_and_creates_new(self):
        archived = SimpleNamespace(name="playlist-track", archived=True)
        created = SimpleNamespace(id=99)
        channel = SimpleNamespace(
            threads=[archived],
            create_thread=AsyncMock(return_value=created),
        )
        result = await botmod._get_or_create_thread(channel, "Song")
        assert result is created
        channel.create_thread.assert_awaited_once()
        kwargs = channel.create_thread.await_args.kwargs
        assert kwargs["name"] == "playlist-track"
        assert kwargs["auto_archive_duration"] == 4320


class TestSendOrUpdateCard:
    async def test_sends_when_no_state(self, monkeypatch):
        thread = SimpleNamespace(id=1, send=AsyncMock(return_value="new-msg"))
        monkeypatch.setattr(botmod, "get_thread_state", AsyncMock(return_value=None))

        assert await botmod._send_or_update_card(thread, "embed") == "new-msg"
        thread.send.assert_awaited_once_with(embed="embed")

    async def test_edits_existing_message(self, monkeypatch):
        message = SimpleNamespace(edit=AsyncMock())
        thread = SimpleNamespace(id=1, fetch_message=AsyncMock(return_value=message))
        monkeypatch.setattr(
            botmod,
            "get_thread_state",
            AsyncMock(return_value={"last_message_id": "555"}),
        )

        result = await botmod._send_or_update_card(thread, "embed")
        thread.fetch_message.assert_awaited_once_with(555)
        message.edit.assert_awaited_once_with(embed="embed")
        assert result is message

    @pytest.mark.parametrize("exc", [discord.NotFound, discord.Forbidden])
    async def test_falls_back_to_new_message_when_edit_fails(self, monkeypatch, exc):
        thread = SimpleNamespace(
            id=1,
            fetch_message=AsyncMock(
                side_effect=exc(MagicMock(status=404), {"message": "gone", "code": 10008})
            ),
            send=AsyncMock(return_value="new-msg"),
        )
        monkeypatch.setattr(
            botmod,
            "get_thread_state",
            AsyncMock(return_value={"last_message_id": "555"}),
        )

        assert await botmod._send_or_update_card(thread, "embed") == "new-msg"
        thread.send.assert_awaited_once()

    async def test_state_without_message_id_sends(self, monkeypatch):
        thread = SimpleNamespace(id=1, send=AsyncMock(return_value="new-msg"))
        monkeypatch.setattr(
            botmod,
            "get_thread_state",
            AsyncMock(return_value={"last_message_id": None}),
        )
        assert await botmod._send_or_update_card(thread, "embed") == "new-msg"


class TestModelConversions:
    def test_parsed_to_model(self):
        message = _message()
        model = botmod._parsed_to_model(_link(), message)
        assert model.platform == Platform.YOUTUBE
        assert model.canonical_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert model.added_by == "tester#0001"
        assert model.message_id == "777"

    def test_link_to_dict(self):
        message = _message()
        result = botmod._link_to_dict(
            _link(Platform.SPOTIFY, "https://open.spotify.com/track/x"), message
        )
        assert result == {
            "platform": "spotify",
            "original_url": "https://open.spotify.com/track/x",
            "canonical_url": "https://open.spotify.com/track/x",
            "track_id": "dQw4w9WgXcQ",
            "added_by": "tester#0001",
            "message_id": "777",
        }


class TestPlaylistLookups:
    async def test_missing_spotify_playlist_is_created_and_saved(self, monkeypatch):
        create = MagicMock(return_value="sp-new")
        monkeypatch.setattr(botmod.playlist_mgr, "create_spotify_playlist", create)

        pid = await botmod._get_playlist_id("chan-1", Platform.SPOTIFY)

        assert pid == "sp-new"
        create.assert_called_once_with("chan-1")
        assert (
            await botmod._get_playlist_url("chan-1", Platform.SPOTIFY)
            == "https://open.spotify.com/playlist/sp-new"
        )

    async def test_existing_playlist_is_returned_without_creating(self, monkeypatch):
        create = MagicMock(return_value="unused")
        monkeypatch.setattr(botmod.playlist_mgr, "create_spotify_playlist", create)
        await dbmod.save_playlist_state(
            "chan-1", Platform.SPOTIFY, "sp-old", "https://open.spotify.com/playlist/sp-old"
        )

        assert await botmod._get_playlist_id("chan-1", Platform.SPOTIFY) == "sp-old"
        create.assert_not_called()

    async def test_missing_youtube_playlist_uses_manager(self, monkeypatch):
        create = MagicMock(return_value="PL-yt")
        monkeypatch.setattr(botmod.playlist_mgr, "_get_or_create_youtube_playlist", create)

        assert await botmod._get_playlist_id("chan-2", Platform.YOUTUBE) == "PL-yt"
        assert (
            await botmod._get_playlist_url("chan-2", Platform.YOUTUBE)
            == "https://youtube.com/playlist?list=PL-yt"
        )

    async def test_unsupported_platform_returns_none(self):
        assert await botmod._get_playlist_id("chan-3", Platform.APPLE_MUSIC) is None
        assert await botmod._get_playlist_url("chan-3", Platform.APPLE_MUSIC) is None

    async def test_create_missing_playlists_saves_both_platforms(self, monkeypatch):
        ids = AsyncMock(side_effect=["PL-yt", "sp-id"])
        save = AsyncMock()
        monkeypatch.setattr(botmod, "_get_playlist_id", ids)
        monkeypatch.setattr(botmod, "save_playlist_state", save)

        await botmod._create_missing_playlists("chan-9")

        assert ids.await_count == 2
        calls = save.await_args_list
        assert calls[0].args == (
            "chan-9",
            Platform.YOUTUBE,
            "PL-yt",
            "https://youtube.com/playlist?list=PL-yt",
        )
        assert calls[1].args == (
            "chan-9",
            Platform.SPOTIFY,
            "sp-id",
            "https://open.spotify.com/playlist/sp-id",
        )

    async def test_get_song_count(self):
        assert await botmod._get_song_count() == 0
        await dbmod.insert_song(
            title="Song",
            artist="Artist",
            links=[
                SimpleNamespace(
                    platform=Platform.YOUTUBE,
                    original_url="https://youtu.be/a",
                    canonical_url="https://youtu.be/a",
                    track_id="a",
                    added_by=None,
                    message_id=None,
                )
            ],
        )
        assert await botmod._get_song_count() == 1


class TestRunBot:
    def test_run_bot_initialises_db_and_playlists(self, monkeypatch):
        loop = botmod.asyncio.new_event_loop()
        monkeypatch.setattr(botmod.asyncio, "new_event_loop", MagicMock(return_value=loop))
        monkeypatch.setattr(botmod.asyncio, "set_event_loop", MagicMock())
        init = AsyncMock()
        missing = AsyncMock()
        run = MagicMock()
        monkeypatch.setattr(botmod, "init_db", init)
        monkeypatch.setattr(botmod, "_create_missing_playlists", missing)
        monkeypatch.setattr(botmod.bot, "run", run)
        monkeypatch.setenv("DISCORD_CHANNEL_ID", "111")
        monkeypatch.setenv("DISCORD_GUILD_ID", "222")

        try:
            botmod.run_bot("token-abc")
        finally:
            loop.close()

        assert init.await_count == 1
        assert missing.await_args_list[0].args == ("111",)
        assert missing.await_args_list[1].args == ("222",)
        run.assert_called_once_with("token-abc")


class TestEvents:
    async def test_on_ready_logs_connection(self, monkeypatch):
        monkeypatch.setattr(botmod, "bot", SimpleNamespace(user="TestBot#0001"))
        await botmod.on_ready()

    async def test_on_message_ignores_bot_authors(self, monkeypatch):
        process = AsyncMock()
        monkeypatch.setattr(botmod.bot, "process_commands", process)
        extract = MagicMock()
        monkeypatch.setattr(botmod, "extract_links", extract)

        await botmod.on_message(_message("https://youtu.be/a", is_bot=True))

        process.assert_not_awaited()
        extract.assert_not_called()

    async def test_on_message_without_links_only_processes_commands(self, monkeypatch):
        process = AsyncMock()
        extract = MagicMock(return_value=[])
        monkeypatch.setattr(botmod.bot, "process_commands", process)
        monkeypatch.setattr(botmod, "extract_links", extract)

        await botmod.on_message(_message("hello"))

        process.assert_awaited_once()
        extract.assert_called_once_with("hello")

    async def test_on_message_skips_known_links(self, monkeypatch):
        monkeypatch.setattr(botmod.bot, "process_commands", AsyncMock())
        monkeypatch.setattr(botmod, "extract_links", MagicMock(return_value=[_link()]))
        known = AsyncMock(return_value=True)
        metadata = AsyncMock()
        monkeypatch.setattr(botmod, "is_known_link", known)
        monkeypatch.setattr(botmod, "extract_metadata", metadata)

        await botmod.on_message(_message("https://youtu.be/a"))

        known.assert_awaited_once()
        metadata.assert_not_awaited()

    async def test_on_message_drops_links_without_metadata(self, monkeypatch):
        monkeypatch.setattr(botmod.bot, "process_commands", AsyncMock())
        monkeypatch.setattr(botmod, "extract_links", MagicMock(return_value=[_link()]))
        monkeypatch.setattr(botmod, "is_known_link", AsyncMock(return_value=False))
        monkeypatch.setattr(botmod, "extract_metadata", AsyncMock(return_value=None))
        thread = SimpleNamespace(id=5, send=AsyncMock())
        monkeypatch.setattr(botmod, "_get_or_create_thread", AsyncMock(return_value=thread))
        insert = AsyncMock()
        monkeypatch.setattr(botmod, "insert_song", insert)

        await botmod.on_message(_message("https://youtu.be/a"))

        insert.assert_not_awaited()
        thread.send.assert_not_awaited()

    async def test_on_message_stores_song_and_updates_card(self, monkeypatch):
        monkeypatch.setattr(botmod.bot, "process_commands", AsyncMock())
        monkeypatch.setattr(botmod, "extract_links", MagicMock(return_value=[_link()]))
        monkeypatch.setattr(botmod, "is_known_link", AsyncMock(return_value=False))

        meta = SimpleNamespace(
            title="Never Gonna Give You Up",
            artist="Rick Astley",
            album="Whenever You Need Somebody",
            duration_ms=213_000,
        )
        monkeypatch.setattr(botmod, "extract_metadata", AsyncMock(return_value=meta))

        thread = SimpleNamespace(id=5)
        monkeypatch.setattr(botmod, "_get_or_create_thread", AsyncMock(return_value=thread))
        monkeypatch.setattr(botmod, "insert_song", AsyncMock(return_value="song-1"))
        monkeypatch.setattr(botmod, "_get_playlist_id", AsyncMock(return_value=None))
        monkeypatch.setattr(botmod, "_get_song_count", AsyncMock(return_value=1))
        sent = SimpleNamespace(id=900)
        send_card = AsyncMock(return_value=sent)
        monkeypatch.setattr(botmod, "_send_or_update_card", send_card)
        save_state = AsyncMock()
        monkeypatch.setattr(botmod, "save_thread_state", save_state)

        message = _message("https://youtu.be/a", channel_id=42, message_id=777)
        await botmod.on_message(message)

        insert_args = botmod.insert_song.await_args.kwargs
        assert insert_args["title"] == "Never Gonna Give You Up"
        assert insert_args["artist"] == "Rick Astley"
        assert insert_args["duration"] == 213_000
        assert insert_args["added_by"] == "tester#0001"
        assert insert_args["message_id"] == "777"

        save_card_args = send_card.await_args.args
        assert save_card_args[0] is thread
        embed = save_card_args[1]
        fields = {field.name: field.value for field in embed.fields}
        assert "Never Gonna Give You Up" in fields["Track"]
        assert "1 songs collected" == embed.footer.text

        state_args = save_state.await_args.kwargs
        assert state_args["thread_id"] == 5
        assert state_args["channel_id"] == "42"
        assert state_args["last_message_id"] == "900"
        assert state_args["total_songs"] == 1
        assert state_args["latest_title"] == "Never Gonna Give You Up"
        assert state_args["latest_url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    async def test_on_message_playlist_success_records_playlist_url(self, monkeypatch):
        monkeypatch.setattr(botmod.bot, "process_commands", AsyncMock())
        monkeypatch.setattr(botmod, "extract_links", MagicMock(return_value=[_link()]))
        monkeypatch.setattr(botmod, "is_known_link", AsyncMock(return_value=False))
        meta = SimpleNamespace(title="T", artist="A", album=None, duration_ms=None)
        monkeypatch.setattr(botmod, "extract_metadata", AsyncMock(return_value=meta))
        monkeypatch.setattr(
            botmod, "_get_or_create_thread", AsyncMock(return_value=SimpleNamespace(id=5))
        )
        monkeypatch.setattr(botmod, "insert_song", AsyncMock(return_value="song-1"))
        monkeypatch.setattr(botmod, "_get_playlist_id", AsyncMock(side_effect=["PL-yt", "sp-id"]))
        monkeypatch.setattr(
            botmod, "_get_playlist_url", AsyncMock(side_effect=["yt-url", "sp-url"])
        )
        add = MagicMock(return_value=True)
        monkeypatch.setattr(botmod.playlist_mgr, "add_to_playlist", add)
        update_url = AsyncMock()
        monkeypatch.setattr(botmod, "update_playlist_url", update_url)
        monkeypatch.setattr(botmod, "_get_song_count", AsyncMock(return_value=1))
        monkeypatch.setattr(
            botmod, "_send_or_update_card", AsyncMock(return_value=SimpleNamespace(id=1))
        )
        save_state = AsyncMock()
        monkeypatch.setattr(botmod, "save_thread_state", save_state)

        await botmod.on_message(_message("https://youtu.be/a"))

        assert add.call_count == 2
        assert update_url.await_count == 2
        update_url.assert_any_await("song-1", Platform.YOUTUBE, "yt-url")
        update_url.assert_any_await("song-1", Platform.SPOTIFY, "sp-url")

    async def test_on_message_playlist_failure_does_not_record_url(self, monkeypatch):
        monkeypatch.setattr(botmod.bot, "process_commands", AsyncMock())
        monkeypatch.setattr(botmod, "extract_links", MagicMock(return_value=[_link()]))
        monkeypatch.setattr(botmod, "is_known_link", AsyncMock(return_value=False))
        meta = SimpleNamespace(title="T", artist="A", album=None, duration_ms=None)
        monkeypatch.setattr(botmod, "extract_metadata", AsyncMock(return_value=meta))
        monkeypatch.setattr(
            botmod, "_get_or_create_thread", AsyncMock(return_value=SimpleNamespace(id=5))
        )
        monkeypatch.setattr(botmod, "insert_song", AsyncMock(return_value="song-1"))
        monkeypatch.setattr(botmod, "_get_playlist_id", AsyncMock(return_value="pid"))
        monkeypatch.setattr(botmod.playlist_mgr, "add_to_playlist", MagicMock(return_value=False))
        update_url = AsyncMock()
        url_lookup = AsyncMock(return_value="url")
        monkeypatch.setattr(botmod, "update_playlist_url", update_url)
        monkeypatch.setattr(botmod, "_get_playlist_url", url_lookup)
        monkeypatch.setattr(botmod, "_get_song_count", AsyncMock(return_value=1))
        monkeypatch.setattr(
            botmod, "_send_or_update_card", AsyncMock(return_value=SimpleNamespace(id=1))
        )
        monkeypatch.setattr(botmod, "save_thread_state", AsyncMock())

        await botmod.on_message(_message("https://youtu.be/a"))

        update_url.assert_not_awaited()
        url_lookup.assert_not_awaited()

    async def test_on_message_ignores_disallowed_channel(self, monkeypatch):
        monkeypatch.setenv("DISCORD_CHANNEL_IDS", "100, 200")
        process = AsyncMock()
        monkeypatch.setattr(botmod.bot, "process_commands", process)

        await botmod.on_message(_message("https://youtu.be/a", channel_id=42))
        process.assert_not_awaited()

        await botmod.on_message(_message("hello", channel_id=100))
        process.assert_awaited_once()

    async def test_create_missing_playlists_empty_channel_noop(self, monkeypatch):
        ids = AsyncMock()
        monkeypatch.setattr(botmod, "_get_playlist_id", ids)
        await botmod._create_missing_playlists("")
        ids.assert_not_awaited()


class TestSlashCommands:
    async def test_playlist_create_cmd_without_credentials(self, monkeypatch):
        monkeypatch.setattr(botmod, "_create_missing_playlists", AsyncMock())
        interaction = SimpleNamespace(
            channel_id=42,
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        await botmod.playlist_create_cmd.callback(interaction)
        interaction.response.send_message.assert_awaited_once()
        msg = interaction.response.send_message.await_args.args[0]
        assert "42" in msg

    async def test_playlist_create_cmd_with_existing_playlists(self, monkeypatch):
        monkeypatch.setattr(botmod, "_create_missing_playlists", AsyncMock())
        await dbmod.save_playlist_state(
            "42", Platform.SPOTIFY, "sp-1", "https://open.spotify.com/playlist/sp-1"
        )
        interaction = SimpleNamespace(
            channel_id=42,
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        await botmod.playlist_create_cmd.callback(interaction)
        msg = interaction.response.send_message.await_args.args[0]
        assert "Spotify" in msg
        assert "https://open.spotify.com/playlist/sp-1" in msg

    async def test_playlist_export_cmd_sends_m3u_file(self):
        await dbmod.insert_song(
            title="Track 1",
            artist="Artist 1",
            duration=180_000,
            links=[
                SimpleNamespace(
                    platform=Platform.YOUTUBE,
                    original_url="https://youtu.be/abc",
                    canonical_url="https://youtu.be/abc",
                    track_id="abc",
                    added_by="u1",
                    message_id="m1",
                )
            ],
            channel_id="42",
        )
        interaction = SimpleNamespace(
            channel_id=42,
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        await botmod.playlist_export_cmd.callback(interaction)
        interaction.response.send_message.assert_awaited_once()
        kwargs = interaction.response.send_message.await_args.kwargs
        assert kwargs["file"].filename == "playlist.m3u"

    async def test_playlist_links_cmd_shows_playlists_and_tracks(self):
        await dbmod.save_playlist_state(
            "42", Platform.YOUTUBE, "PL-1", "https://youtube.com/playlist?list=PL-1"
        )
        await dbmod.insert_song(
            title="Song X",
            artist="Artist X",
            links=[
                SimpleNamespace(
                    platform=Platform.YOUTUBE,
                    original_url="https://youtu.be/x",
                    canonical_url="https://youtu.be/x",
                    track_id="x",
                    added_by="u1",
                    message_id="m1",
                )
            ],
            channel_id="42",
        )
        interaction = SimpleNamespace(
            channel_id=42,
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        await botmod.playlist_links_cmd.callback(interaction)
        msg = interaction.response.send_message.await_args.args[0]
        assert "Youtube: https://youtube.com/playlist?list=PL-1" in msg
        assert "Song X" in msg

    async def test_playlist_stats_cmd_shows_counts(self):
        song_id = await dbmod.insert_song(
            title="Song Y",
            artist="Artist Y",
            links=[
                SimpleNamespace(
                    platform=Platform.SPOTIFY,
                    original_url="https://open.spotify.com/track/y",
                    canonical_url="https://open.spotify.com/track/y",
                    track_id="y",
                    added_by="alice",
                    message_id="m2",
                )
            ],
            added_by="alice",
            channel_id="42",
        )
        await dbmod.update_playlist_url(
            song_id, Platform.SPOTIFY, "https://open.spotify.com/playlist/sp"
        )
        channels = await dbmod.get_channels()
        assert "42" in channels

        interaction = SimpleNamespace(
            channel_id=42,
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        await botmod.playlist_stats_cmd.callback(interaction)
        msg = interaction.response.send_message.await_args.args[0]
        assert "Songs: **1**" in msg
        assert "Spotify: 1" in msg

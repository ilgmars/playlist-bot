"""Tests for the ``playlistbot.__main__`` entry point."""

from __future__ import annotations

import runpy
import sys
import types
from unittest.mock import MagicMock

import pytest

import playlistbot.__main__ as mainmod


@pytest.fixture()
def uvicorn_stub(monkeypatch):
    stub = types.SimpleNamespace(run=MagicMock())
    monkeypatch.setitem(sys.modules, "uvicorn", stub)
    return stub


@pytest.fixture()
def process_stub(monkeypatch):
    process = MagicMock()
    module = types.SimpleNamespace(Process=MagicMock(return_value=process))
    monkeypatch.setitem(sys.modules, "multiprocessing", module)
    return module, process


@pytest.fixture()
def run_bot_stub(monkeypatch):
    stub = MagicMock()
    monkeypatch.setattr("playlistbot.bot.run_bot", stub)
    return stub


class TestWebRole:
    def test_defaults_to_web(self, monkeypatch, uvicorn_stub):
        monkeypatch.delenv("ROLE", raising=False)
        monkeypatch.delenv("WEB_HOST", raising=False)
        monkeypatch.delenv("WEB_PORT", raising=False)

        mainmod.main()

        uvicorn_stub.run.assert_called_once_with(
            "playlistbot.web:app",
            host="0.0.0.0",
            port=8000,
            log_level="info",
        )

    def test_custom_host_and_port(self, monkeypatch, uvicorn_stub):
        monkeypatch.setenv("ROLE", "web")
        monkeypatch.setenv("WEB_HOST", "127.0.0.1")
        monkeypatch.setenv("WEB_PORT", "9001")

        mainmod.main()

        uvicorn_stub.run.assert_called_once_with(
            "playlistbot.web:app",
            host="127.0.0.1",
            port=9001,
            log_level="info",
        )


class TestBotRole:
    def test_runs_bot_with_token(self, monkeypatch, run_bot_stub):
        monkeypatch.setenv("ROLE", "bot")
        monkeypatch.setenv("DISCORD_TOKEN", "token-abc")

        mainmod.main()

        run_bot_stub.assert_called_once_with("token-abc")

    def test_exits_without_token(self, monkeypatch, run_bot_stub):
        monkeypatch.setenv("ROLE", "bot")
        monkeypatch.delenv("DISCORD_TOKEN", raising=False)

        with pytest.raises(SystemExit) as excinfo:
            mainmod.main()

        assert excinfo.value.code == 1
        run_bot_stub.assert_not_called()


class TestAllRole:
    def test_starts_web_process_and_bot(
        self, monkeypatch, uvicorn_stub, process_stub, run_bot_stub
    ):
        module, process = process_stub
        monkeypatch.setenv("ROLE", "all")
        monkeypatch.setenv("DISCORD_TOKEN", "token-abc")

        mainmod.main()

        module.Process.assert_called_once()
        process.start.assert_called_once_with()
        run_bot_stub.assert_called_once_with("token-abc")

        # The spawned target boots the same ASGI app on 0.0.0.0:8000.
        target = module.Process.call_args.kwargs["target"]
        target()
        uvicorn_stub.run.assert_called_once_with(
            "playlistbot.web:app",
            host="0.0.0.0",
            port=8000,
            log_level="info",
        )

    def test_exits_without_token(self, monkeypatch, uvicorn_stub, process_stub, run_bot_stub):
        module, process = process_stub
        monkeypatch.setenv("ROLE", "all")
        monkeypatch.delenv("DISCORD_TOKEN", raising=False)

        with pytest.raises(SystemExit) as excinfo:
            mainmod.main()

        assert excinfo.value.code == 1
        module.Process.assert_not_called()
        process.start.assert_not_called()
        run_bot_stub.assert_not_called()


class TestUnknownRole:
    def test_exits(self, monkeypatch, uvicorn_stub):
        monkeypatch.setenv("ROLE", "worker")

        with pytest.raises(SystemExit) as excinfo:
            mainmod.main()

        assert excinfo.value.code == 1
        uvicorn_stub.run.assert_not_called()


class TestModuleGuard:
    def test_runs_main_when_executed_as_script(self, monkeypatch, uvicorn_stub):
        monkeypatch.setenv("ROLE", "web")

        runpy.run_module("playlistbot.__main__", run_name="__main__", alter_sys=True)

        uvicorn_stub.run.assert_called_once()

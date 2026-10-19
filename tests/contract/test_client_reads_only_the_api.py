"""Contract (v2.6, LUMI-229): a client reads the server's state only through the API.

From v2.6 the server lives on the Linux box and the Mac runs only a client, so the client must never reach
for a data root — not even when its own ``.env`` points ``LUMI_HOME`` at one full of the server's files. A
client-mode TUI runs a turn and ``/latency`` here while every open under that root, every SQLite connect
and every core/repository construction is recorded: the record must stay empty. No sockets, no paid API.
"""

import builtins
import io
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.agent import Core
from core.config import load_config
from core.llm import MockLLMClient
from server.app import create_app
from state.local_store import JsonRepository
from tui.app import LumiApp
from tui.remote import RemoteCore

TOKEN = "api-only-token"
_SERVER_FILES = ("store.json", "store.db", "mood.log", "schedule.state", "inbox.jsonl", "outbox.jsonl")


@pytest.fixture
def watched_root(tmp_path, monkeypatch):
    """A data root full of server files that the client's config points at — and a record of any touch."""
    root = (tmp_path / "server-root").resolve()
    root.mkdir()
    for name in _SERVER_FILES:
        (root / name).write_text("{}", encoding="utf-8")
    monkeypatch.setenv("LUMI_HOME", str(root))
    for key in ("LUMI_BRIDGE", "LUMI_SCHEDULER", "LUMI_MODE_SET", "LUMI_DICTATION", "LUMI_VOICE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("tui.app.load_config", lambda: load_config(load_env=False))
    return root


def _watch(monkeypatch, root: Path) -> list[str]:
    touched: list[str] = []
    real_open, real_connect = io.open, sqlite3.connect

    def under_root(target) -> bool:
        try:
            path = Path(target).resolve()
        except TypeError:  # a file descriptor
            return False
        return path == root or root in path.parents

    def guarded_open(file, *args, **kwargs):
        if under_root(file):
            touched.append(f"open {file}")
        return real_open(file, *args, **kwargs)

    def guarded_connect(database, *args, **kwargs):
        if under_root(database):
            touched.append(f"sqlite {database}")
        return real_connect(database, *args, **kwargs)

    def no_core(*_, **__):
        touched.append("a core was built")
        raise AssertionError("a client never builds a core")

    def no_repository(self, *_, **__):
        touched.append(f"a {type(self).__name__} was built")
        raise AssertionError("a client never builds a repository")

    monkeypatch.setattr(io, "open", guarded_open)
    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(sqlite3, "connect", guarded_connect)
    monkeypatch.setattr("core.agent.build_core", no_core)
    monkeypatch.setattr(JsonRepository, "__init__", no_repository)  # SqliteRepository inherits it
    return touched


async def test_a_client_turn_and_latency_never_touch_a_data_root(tmp_path, watched_root, monkeypatch):
    core = Core(llm=MockLLMClient(states={"reply": "Радо!", "emotion": "joy", "intensity": 0.8}),
                repository=JsonRepository(tmp_path / "server" / "s.json"), canon="Ти — Лілі.", model="m",
                mood_enabled=False)
    remote = RemoteCore("http://testserver", TOKEN, client=TestClient(create_app(core, token=TOKEN, env="dev")))
    remote.connect()
    touched = _watch(monkeypatch, watched_root)  # from here on, the client side runs
    (watched_root / "mood.log").read_text(encoding="utf-8")  # the watch itself works: a read is recorded…
    with pytest.raises(AssertionError):
        JsonRepository(watched_root / "store.json")  # …and so is a repository
    assert len(touched) == 2
    touched.clear()
    app = LumiApp(remote)
    async with app.run_test() as pilot:
        for text, until in (("привіт", "Радо!"), ("/latency", "NETWORK client↔server")):
            app.query_one("#prompt").text = text
            await pilot.press("enter")
            for _ in range(120):
                await pilot.pause()
                if any(until in line for line in app.transcript):
                    break
        assert any("NETWORK client↔server" in line for line in app.transcript)
    assert touched == []  # the server's state came through the API only

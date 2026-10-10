"""The TUI client lives on pushes (LUMI-221) — the real app over real uvicorn on an ephemeral localhost port,
with a mock-model core: a thought fired from another client arrives unasked, the status line follows a change
made elsewhere, the TUI's own ``%think!`` shows once, a stopped server is said once and a new one picked up.
No paid API; hermetic config (``tui.app.load_config`` reads the environment only)."""

import asyncio
import signal
import threading
import time
from types import SimpleNamespace

import pytest

import tui.remote as remote
from core.agent import Core
from core.config import load_config
from core.llm import MockLLMClient
from server.__main__ import make_server
from server.app import create_app
from state.local_store import JsonRepository
from tui.app import PUSHES_BACK, PUSHES_LOST, LumiApp
from tui.remote import RemoteCore

TOKEN = "push-test-token"
THOUGHT = "хмари сьогодні пишуть листи"


@pytest.fixture
def client_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMI_HOME", str(tmp_path / "client-root"))
    for key in ("LUMI_BRIDGE", "LUMI_SCHEDULER", "LUMI_MODE_SET", "LUMI_DICTATION", "LUMI_VOICE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("tui.app.load_config", lambda: load_config(load_env=False))
    monkeypatch.setattr(remote, "LISTEN_BACKOFF_S", (0.05, 0.1))


def _wait(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def _serve(tmp_path, port=0):
    core = Core(llm=MockLLMClient(f"{THOUGHT}\nЕМОЦІЯ: playful",
                                  states={"reply": "Радо!", "emotion": "joy", "intensity": 0.8}),
                repository=JsonRepository(tmp_path / "server" / "s.json"), canon="Ти — Лілі.", model="m",
                mood_enabled=False, styles={"warm": "тепло"})
    app = create_app(core, token=TOKEN, env="dev", version="2.4.0")
    server = make_server(app, host="127.0.0.1", port=port)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait(lambda: server.started)
    port = server.servers[0].sockets[0].getsockname()[1]
    return SimpleNamespace(url=f"http://127.0.0.1:{port}", port=port, app=app, server=server, thread=thread)


def _stop(live):
    live.server.handle_exit(signal.SIGINT, None)  # the first Ctrl+C
    live.thread.join(5)


@pytest.fixture
def live(tmp_path):
    srv = _serve(tmp_path)
    yield srv
    _stop(srv)


def _tui(url):
    rc = RemoteCore(url, TOKEN)
    rc.connect()
    return LumiApp(rc)


async def _until(pilot, cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "timed out"
        await pilot.pause(0.02)


def _listening(live):
    return lambda: live.app.state.service.bus.listeners >= 1


async def test_a_thought_fired_elsewhere_arrives_unasked(live, client_env):
    app = _tui(live.url)
    async with app.run_test() as pilot:
        await _until(pilot, _listening(live))
        cli = RemoteCore(live.url, TOKEN)  # ./lumi-cli think
        await asyncio.to_thread(cli.run_directive, "%think! про хмари")
        await _until(pilot, lambda: any(THOUGHT in line for line in app.transcript))
        assert [line for line in app.transcript if "💭" in line] == [f"💭 {THOUGHT}"]


async def test_the_status_line_follows_a_change_made_elsewhere(live, client_env):
    app = _tui(live.url)
    shown: list[str] = []
    real = app._render_status

    def spy(busy=None):
        shown.append(app._status_text(busy))
        return real(busy)

    app._render_status = spy
    async with app.run_test() as pilot:
        await _until(pilot, _listening(live))
        assert not any("warm" in s for s in shown)
        cli = RemoteCore(live.url, TOKEN)  # ./lumi-cli … from another terminal
        await asyncio.to_thread(cli.command, "/style warm")
        await _until(pilot, lambda: any("warm" in s for s in shown))  # re-rendered from the push alone


async def test_its_own_think_shows_once(live, client_env):
    app = _tui(live.url)
    async with app.run_test() as pilot:
        await _until(pilot, _listening(live))
        app.query_one("#prompt").text = "%think! про хмари"
        await pilot.press("enter")
        await _until(pilot, lambda: any(THOUGHT in line for line in app.transcript))
        await pilot.pause(0.3)  # time for an echo to arrive — it must not
        assert [line for line in app.transcript if "💭" in line] == [f"💭 {THOUGHT}"]


async def test_a_stopped_server_is_said_once_and_a_new_one_picked_up(tmp_path, client_env):
    first = _serve(tmp_path)
    app = _tui(first.url)
    async with app.run_test() as pilot:
        await _until(pilot, _listening(first))
        _stop(first)
        await _until(pilot, lambda: PUSHES_LOST in app.transcript)
        assert not app._connected
        await pilot.pause(0.3)  # several failed reconnects meanwhile…
        assert app.transcript.count(PUSHES_LOST) == 1  # …said once
        second = _serve(tmp_path, port=first.port)  # the server is back
        try:
            await _until(pilot, lambda: PUSHES_BACK in app.transcript)
            assert app._connected
        finally:
            _stop(second)

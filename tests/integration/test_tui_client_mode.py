"""The TUI in client mode (LUMI-213) — the real app over RemoteCore → a TestClient-backed server.

Hermetic: ``tui.app.load_config`` reads the environment only, and ``LUMI_HOME`` points at a directory
the client must never create — a client mode TUI never touches a data root.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from core.agent import Core
from core.commands import MOOD_PENDING
from core.config import load_config
from core.llm import MockLLMClient
from server.app import create_app
from state.local_store import JsonRepository
from tui.app import LumiApp
from tui.remote import NOT_YET, RemoteCore

TOKEN = "tui-client-token"


@pytest.fixture
def client_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMI_HOME", str(tmp_path / "client-root"))
    for key in ("LUMI_BRIDGE", "LUMI_SCHEDULER", "LUMI_MODE_SET", "LUMI_DICTATION", "LUMI_VOICE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("tui.app.load_config", lambda: load_config(load_env=False))
    return monkeypatch


def _setup(tmp_path):
    core = Core(llm=MockLLMClient(states={"reply": "Радо!", "emotion": "joy", "intensity": 0.8}),
                repository=JsonRepository(tmp_path / "server" / "s.json"),
                canon="Ти — Лілі.", model="claude-haiku-4-5-20251001")
    server = create_app(core, token=TOKEN, env="dev", version="2.2.0")
    remote = RemoteCore("http://testserver", TOKEN, client=TestClient(server))
    remote.connect()
    return LumiApp(remote), server


async def _submit(pilot, app, text, *, until=None):
    app.query_one("#prompt").text = text
    await pilot.press("enter")
    for _ in range(120):
        await pilot.pause()
        if until is not None and any(until in line for line in app.transcript):
            return


async def test_a_turn_runs_on_the_server(tmp_path, client_env):
    app, server = _setup(tmp_path)
    async with app.run_test() as pilot:
        await _submit(pilot, app, "привіт", until="Радо!")
        assert any("Радо!" in line for line in app.transcript)
        assert server.state.service.core.totals.turns == 1  # the server's core took the turn
        assert "haiku" in app._status_text()  # the status line reads the server's snapshot


async def test_a_layer_command_runs_on_the_server(tmp_path, client_env):
    app, _ = _setup(tmp_path)
    async with app.run_test() as pilot:
        await _submit(pilot, app, "/mood", until=MOOD_PENDING)
        assert any(MOOD_PENDING in line for line in app.transcript)


async def test_new_switches_the_server_session(tmp_path, client_env):
    app, server = _setup(tmp_path)
    async with app.run_test() as pilot:
        before = server.state.service.session.id
        await _submit(pilot, app, "/new", until="new session")
        assert server.state.service.session.id != before


@pytest.mark.parametrize("line", ["%think", "/mode-set voice"])
async def test_not_yet_features_say_so(tmp_path, client_env, line):
    app, _ = _setup(tmp_path)
    async with app.run_test() as pilot:
        await _submit(pilot, app, line, until=NOT_YET)
        assert any(NOT_YET in entry for entry in app.transcript)


async def test_enabled_features_the_server_cant_host_are_announced(tmp_path, client_env):
    client_env.setenv("LUMI_BRIDGE", "on")
    app, _ = _setup(tmp_path)
    async with app.run_test() as pilot:
        for _ in range(20):
            await pilot.pause()
        assert any("Telegram bridge" in line and NOT_YET in line for line in app.transcript)
        assert app._bridge is False  # off in the client


async def test_a_server_that_drops_mid_session_is_a_readable_line(tmp_path, client_env):
    app_srv = create_app(Core(llm=MockLLMClient("x"), repository=JsonRepository(tmp_path / "s.json"),
                              canon="C", model="m"), token=TOKEN)
    live = TestClient(app_srv)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/turn":
            raise httpx.ConnectError("server gone", request=request)
        res = live.request(request.method, request.url.path, headers=dict(request.headers),
                           content=request.content)
        return httpx.Response(res.status_code, json=res.json())

    remote = RemoteCore("http://testserver", TOKEN,
                        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://testserver"))
    remote.connect()
    app = LumiApp(remote)
    async with app.run_test() as pilot:
        await _submit(pilot, app, "привіт", until="unreachable")
        assert any("⚠ server unreachable" in line for line in app.transcript)
        assert app.query_one("#prompt").disabled is False  # the loop is alive; the next input retries


async def test_the_client_never_touches_a_data_root(tmp_path, client_env):
    app, _ = _setup(tmp_path)
    async with app.run_test() as pilot:
        await _submit(pilot, app, "привіт", until="Радо!")
        await _submit(pilot, app, "/mood", until=MOOD_PENDING)
    assert not (tmp_path / "client-root").exists()


async def test_chat_lines_never_cost_a_command_round_trip(tmp_path, client_env):
    app, server = _setup(tmp_path)
    paths: list[str] = []
    remote = app._core
    real = remote._http.request

    def spy(method, path, **kw):
        paths.append(path)
        return real(method, path, **kw)

    remote._http.request = spy
    async with app.run_test() as pilot:
        await _submit(pilot, app, "привіт", until="Радо!")
    assert "/v1/turn" in paths and "/v1/command" not in paths

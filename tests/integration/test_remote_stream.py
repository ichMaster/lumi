"""RemoteCore streams a turn (LUMI-217) — into the TUI's callbacks, and never twice when the stream breaks.

The server is the real app over a mock-model core (the v1.4 stream seam on); fault transports wrap it to
drop / garble the stream. No sockets, no paid API.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from core.agent import Core
from core.config import load_config
from core.llm import MockLLMClient
from server.app import create_app
from state.local_store import JsonRepository
from tui.app import LumiApp
from tui.remote import RemoteCore, ServerUnavailable

TOKEN = "remote-stream-token"
REPLY = "Привіт, як ти сьогодні ввечері?"


def _server(tmp_path, *, stream=True, reply=REPLY):
    core = Core(llm=MockLLMClient(states={"reply": reply, "emotion": "joy", "intensity": 0.7}, stream_chunk=4),
                repository=JsonRepository(tmp_path / "s.json"), canon="Ти — Лілі.",
                model="claude-haiku-4-5-20251001", stream=stream)
    return create_app(core, token=TOKEN, env="dev", version="2.3.0"), core


class _Fault(httpx.BaseTransport):
    """Forwards to the app, but breaks /v1/turn/stream: ``drop`` after N bytes, or ``garble`` / ``cut`` it."""

    def __init__(self, app, mode: str, after: int = 40) -> None:
        self.inner = TestClient(app)._transport
        self.mode, self.after = mode, after
        self.paths: list[tuple[str, str | None]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        import json as _json

        turn_id = None
        if request.method == "POST":
            turn_id = _json.loads(request.read() or b"{}").get("turn_id")
        self.paths.append((request.url.path, turn_id))
        res = self.inner.handle_request(request)
        if request.url.path != "/v1/turn/stream":
            return res
        body = res.read()
        mode, after = self.mode, self.after

        class Broken(httpx.SyncByteStream):
            def __iter__(self):
                if mode == "drop":
                    yield body[:after]
                    raise httpx.ReadError("connection dropped")
                if mode == "garble":
                    yield 'event: delta\ndata: {"text": "Пр"}\n\nevent: done\ndata: {"reply": "обірв\n\n'.encode()
                if mode == "cut":  # a clean end — but no outcome
                    yield 'event: delta\ndata: {"text": "Пр"}\n\n'.encode()

        return httpx.Response(res.status_code, headers=res.headers, stream=Broken(), request=request)


def _remote(app_or_transport, *, via_testclient=True):
    if via_testclient:
        client = TestClient(app_or_transport)
    else:
        client = httpx.Client(transport=app_or_transport, base_url="http://testserver")
    rc = RemoteCore("http://testserver", TOKEN, client=client)
    rc.connect()
    return rc


def test_a_streamed_turn_feeds_the_callbacks_and_matches_the_blocking_reply(tmp_path):
    app, _ = _server(tmp_path)
    rc = _remote(app)
    assert rc.stream_enabled is True
    deltas: list[str] = []
    state = rc.reply("привіт", None, on_delta=deltas.append)
    assert len(deltas) > 1 and "".join(deltas) == state.reply == REPLY
    assert state.emotion.value == "joy"  # from `done`, validated on completion


def test_think_reaches_the_think_callback(tmp_path):
    app, _ = _server(tmp_path, reply="<think>зважую</think>Готово")
    rc = _remote(app)
    think: list[str] = []
    shown: list[str] = []
    rc.reply("привіт", None, on_delta=shown.append, on_think_delta=think.append)
    assert "зважую" in "".join(think) and "".join(shown) == "Готово"


@pytest.mark.parametrize("mode", ["drop", "garble", "cut"])
def test_a_broken_stream_falls_back_by_turn_id_and_she_answers_once(tmp_path, mode):
    app, core = _server(tmp_path)
    fault = _Fault(app, mode)
    rc = _remote(fault, via_testclient=False)
    state = rc.reply("привіт", None, on_delta=lambda _t: None)
    assert state.reply == REPLY
    assert core.totals.turns == 1  # never twice
    turns = [(p, tid) for p, tid in fault.paths if p.startswith("/v1/turn")]
    assert [p for p, _ in turns] == ["/v1/turn/stream", "/v1/turn"]
    assert turns[0][1] == turns[1][1]  # the fallback asked for the SAME turn


def test_no_callbacks_or_a_non_streaming_server_stays_blocking(tmp_path):
    app, _ = _server(tmp_path, stream=False)
    fault = _Fault(app, "drop")
    rc = _remote(fault, via_testclient=False)
    assert rc.stream_enabled is False
    rc.reply("привіт", None, on_delta=lambda _t: None)
    assert "/v1/turn/stream" not in [p for p, _ in fault.paths]


def test_a_server_that_is_down_is_a_readable_unavailable():
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    rc = RemoteCore("http://127.0.0.1:1", TOKEN,
                    client=httpx.Client(transport=httpx.MockTransport(refuse), base_url="http://127.0.0.1:1"))
    rc._state = {"stream_enabled": True}  # it WAS streaming before it went down
    with pytest.raises(ServerUnavailable, match="unreachable"):
        rc.reply("привіт", None, on_delta=lambda _t: None)


@pytest.fixture
def client_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMI_HOME", str(tmp_path / "client-root"))
    for key in ("LUMI_BRIDGE", "LUMI_SCHEDULER", "LUMI_MODE_SET", "LUMI_DICTATION", "LUMI_VOICE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("tui.app.load_config", lambda: load_config(load_env=False))


async def test_the_tui_client_grows_the_reply_live(tmp_path, client_env):
    app_srv, _ = _server(tmp_path)
    app = LumiApp(_remote(app_srv))
    grown: list[str] = []
    real = app._grow_stream_reply

    def spy(chunk):
        grown.append(chunk)
        return real(chunk)

    app._grow_stream_reply = spy
    async with app.run_test() as pilot:
        app.query_one("#prompt").text = "привіт"
        await pilot.press("enter")
        for _ in range(120):
            await pilot.pause()
            if any(REPLY in line for line in app.transcript):
                break
        assert len(grown) > 1 and "".join(grown) == REPLY  # it streamed into the in-flow widget
        assert any(REPLY in line for line in app.transcript)  # …and finalized as the reply

"""`RemoteCore` over the v2.2 server (LUMI-213) — a TestClient transport, no sockets, no paid API."""

import httpx
import pytest
from fastapi.testclient import TestClient

from core.agent import Core
from core.commands import MOOD_PENDING, CommandResult
from core.config import load_config
from core.emotion import Emotion
from core.llm import LLMError, MockLLMClient
from server.app import create_app
from state.local_store import JsonRepository
from tui.remote import (
    NotInServerMode,
    RemoteCore,
    RemoteSession,
    ServerAuthError,
    ServerBusy,
    ServerUnavailable,
    client_mode_config,
)

TOKEN = "remote-test-token"


def _server(tmp_path, llm=None):
    core = Core(llm=llm or MockLLMClient(states={"reply": "Радо!", "emotion": "joy", "intensity": 0.8}),
                repository=JsonRepository(tmp_path / "s.json"), canon="Ти — Лілі.", model="claude-haiku-4-5-20251001")
    return create_app(core, token=TOKEN, env="dev", version="2.2.0")


def _remote(tmp_path, *, token=TOKEN, llm=None):
    app = _server(tmp_path, llm)
    return RemoteCore("http://testserver", token, client=TestClient(app)), app


def test_connect_and_the_snapshot(tmp_path):
    rc, _ = _remote(tmp_path)
    rc.connect()
    assert rc.model == "claude-haiku-4-5-20251001" and rc.totals.turns == 0 and rc.last_emotion is None


def test_a_wrong_token_is_unavailable(tmp_path):
    rc, _ = _remote(tmp_path, token="nope")
    with pytest.raises(ServerAuthError, match="rejected the token"):  # a ServerUnavailable too
        rc.connect()


def test_an_unreachable_server_is_unavailable():
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    rc = RemoteCore("http://127.0.0.1:1", TOKEN, client=httpx.Client(transport=httpx.MockTransport(refuse),
                                                                       base_url="http://127.0.0.1:1"))
    with pytest.raises(ServerUnavailable, match="unreachable"):
        rc.connect()


def test_a_turn_updates_the_snapshot(tmp_path):
    rc, _ = _remote(tmp_path)
    rc.connect()
    state = rc.reply("привіт", rc.start_session())
    assert state.reply == "Радо!" and state.emotion is Emotion.JOY and state.intensity == 0.8
    assert rc.totals.turns == 1 and rc.last_emotion.emotion is Emotion.JOY
    assert rc.last_stats is not None


def test_commands_go_through_the_server_layer(tmp_path):
    rc, _ = _remote(tmp_path)
    rc.connect()
    assert rc.command("/mood") == CommandResult(MOOD_PENDING)
    assert rc.command("/nope") is None
    assert rc.command("/forget").confirm  # the confirm protocol survives the wire


def test_end_session_starts_the_next_one_on_the_server(tmp_path):
    rc, _ = _remote(tmp_path)
    rc.connect()
    first = rc.start_session()
    rc.end_session(first)
    second = rc.start_session()
    assert isinstance(second, RemoteSession) and second.id != first.id


def test_busy_and_model_errors_are_readable(tmp_path):
    rc, app = _remote(tmp_path)
    rc.connect()
    app.state.service.lock.acquire()
    try:
        with pytest.raises(ServerBusy):
            rc.reply("привіт", None)
    finally:
        app.state.service.lock.release()

    def boom(system, messages, model):
        raise LLMError("model down")

    rc2, _ = _remote(tmp_path / "b", llm=MockLLMClient(boom))
    rc2.connect()
    with pytest.raises(LLMError, match="model unavailable: model down"):
        rc2.reply("?", None)


def test_the_server_does_its_own_housekeeping(tmp_path):
    rc, _ = _remote(tmp_path)
    assert rc.ensure_mood() is None and rc.ensure_backfill() is None and rc.set_world_context({}) is None


def test_the_thought_stream_is_not_yet_in_server_mode(tmp_path):
    rc, _ = _remote(tmp_path)
    with pytest.raises(NotInServerMode):
        rc.tick_think()
    with pytest.raises(NotInServerMode):
        rc.run_directive("%think", None)


def test_client_mode_config_turns_off_and_names_what_the_server_cant_host(monkeypatch):
    for key, value in {"LUMI_BRIDGE": "on", "LUMI_SCHEDULER": "on", "LUMI_MODE_SET": "voice"}.items():
        monkeypatch.setenv(key, value)
    cfg, was_on = client_mode_config(load_config(load_env=False))
    assert {"Telegram bridge", "scheduler", "voice mode"} <= set(was_on)
    assert not cfg.bridge and not cfg.scheduler and cfg.mode_set == "text"
    assert not cfg.thoughts and not cfg.idle_nudge and not cfg.voice and not cfg.dictation

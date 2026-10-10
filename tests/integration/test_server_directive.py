"""v2.4 directives on the server (LUMI-220) — POST /v1/directive runs a typed %directive; an open thought is
pushed to every listener as ``thought``, a silent one is not. A mock-model core; no paid API."""

from fastapi.testclient import TestClient

from core.agent import Core
from core.llm import MockLLMClient
from server.app import create_app
from state.local_store import JsonRepository
from tests.integration.test_server_events import AUTH, TOKEN, _listen

ME = {**AUTH, "X-Lumi-Client": "cli-7"}
THOUGHT = "думаю про море і про тишу\nЕМОЦІЯ: tender"


def _client(tmp_path, **kw):
    core = Core(llm=MockLLMClient(THOUGHT, states={"reply": "так", "emotion": "calm", "intensity": 0.5}),
                repository=JsonRepository(tmp_path / "s.json"), canon="Ти — Лілі.", model="m",
                mood_enabled=False, **kw)
    return TestClient(create_app(core, token=TOKEN, env="dev", version="2.4.0")), core


def _fire(client, line, headers=ME):
    return client.post("/v1/directive", json={"line": line}, headers=headers)


def test_an_open_thought_comes_back_and_reaches_every_listener(tmp_path):
    client, _ = _client(tmp_path)
    replies = []
    heard = _listen(client, lambda: replies.append(_fire(client, "%think! про море")), listeners=2)
    res = replies[0].json()
    assert res["is_directive"] is True and res["mode"] == "open"
    assert set(res["thought"]) == {"kind", "text", "emotion", "when"}
    assert res["thought"]["kind"] == "think" and res["thought"]["text"] == "думаю про море і про тишу"
    assert res["thought"]["emotion"] == "tender"
    for events in heard:
        pushed = [d for e, d in events if e == "thought"]
        assert pushed == [{**res["thought"], "origin": "cli-7"}]  # exactly once, with the requester's id


def test_a_silent_thought_is_recorded_but_never_pushed(tmp_path):
    client, core = _client(tmp_path)
    replies = []
    [heard] = _listen(client, lambda: replies.append(_fire(client, "%think")))
    assert replies[0].json()["mode"] == "silent" and replies[0].json()["thought"] is not None
    assert "thought" not in [e for e, _ in heard]
    assert core._repo.thoughts_since("2000-01-01")  # it landed in her diary


def test_a_line_that_isnt_a_directive_comes_back_as_chat(tmp_path):
    client, _ = _client(tmp_path)
    res = _fire(client, "%bogus привіт").json()
    assert res["is_directive"] is False and res["thought"] is None


def test_with_the_thought_stream_off_no_thought_is_pushed(tmp_path):
    client, _ = _client(tmp_path, thoughts_enabled=False)
    replies = []
    [heard] = _listen(client, lambda: replies.append(_fire(client, "%think!")))
    assert replies[0].json()["thought"] is None
    assert "thought" not in [e for e, _ in heard]


def test_a_directive_waits_its_turn(tmp_path):
    client, _ = _client(tmp_path)
    service = client.app.state.service
    service.lock.acquire()  # a turn is running
    try:
        assert _fire(client, "%think!").status_code == 409
    finally:
        service.lock.release()
    assert _fire(client, "%think!").status_code == 200  # the lock was never kept


def test_the_directive_route_needs_the_token(tmp_path):
    client, _ = _client(tmp_path)
    assert _fire(client, "%think!", headers={}).status_code == 401

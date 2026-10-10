"""The v2.4 push channel over the API (LUMI-219) — GET /v1/events: the first state, the per-turn face event,
state on change, the origin rule, a consistent snapshot mid-turn, and a stop that a listener can't hold.

FastAPI's TestClient against a mock-model core (it buffers a response until the stream ends, so each test
ends the stream by closing the bus — exactly what the server's shutdown does); one test runs real uvicorn on
an ephemeral localhost port. No paid API.
"""

import json
import signal
import threading
import time

import httpx
from fastapi.testclient import TestClient

from core.agent import Core
from core.llm import LLMError, MockLLMClient
from server.app import create_app
from state.local_store import JsonRepository

TOKEN = "events-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ME = {**AUTH, "X-Lumi-Client": "tui-1"}


def _client(tmp_path, llm=None):
    core = Core(llm=llm or MockLLMClient(states={"reply": "Радо!", "emotion": "joy", "intensity": 0.8}),
                repository=JsonRepository(tmp_path / "s.json"), canon="Ти — Лілі.",
                model="claude-haiku-4-5-20251001", stream=True, styles={"warm": "тепло"})
    return TestClient(create_app(core, token=TOKEN, env="dev", version="2.4.0")), core


def _wait(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def _events(raw: str) -> list[tuple[str, dict]]:
    out = []
    for frame in raw.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in frame.splitlines() if not line.startswith(":"))
        if "event" in fields:
            out.append((fields["event"], json.loads(fields["data"])))
    return out


def _listen(client, action, *, listeners=1) -> list[list[tuple[str, dict]]]:
    """Attach ``listeners`` to /v1/events, run ``action``, end the streams (the bus closes, as at shutdown)
    and return what each one heard."""
    service = client.app.state.service
    results: list = [None] * listeners

    def listen(i):
        results[i] = client.get("/v1/events", headers=AUTH)

    threads = [threading.Thread(target=listen, args=(i,)) for i in range(listeners)]
    for t in threads:
        t.start()
    _wait(lambda: service.bus.listeners == listeners)
    action()
    service.bus.close()
    for t in threads:
        t.join(5)
    for res in results:
        assert res.status_code == 200 and res.headers["content-type"].startswith("text/event-stream")
    return [_events(res.text) for res in results]


def test_a_listener_starts_from_the_current_state(tmp_path):
    client, _ = _client(tmp_path)
    [heard] = _listen(client, lambda: None)
    assert heard == [("state", {"origin": None, "state": client.get("/v1/state", headers=AUTH).json()})]
    assert set(heard[0][1]["state"]["closeness"]) == {"level", "name"}


def test_a_turn_pushes_its_face_then_the_new_state(tmp_path):
    client, _ = _client(tmp_path)
    replies = []
    [heard] = _listen(client, lambda: replies.append(client.post("/v1/turn", json={"text": "привіт"}, headers=ME)))
    kinds = [e for e, _ in heard]
    assert kinds == ["state", "turn", "state"]
    turn = heard[1][1]
    assert turn == {"emotion": "joy", "intensity": 0.8, "theme": turn["theme"], "origin": "tui-1"}
    pushed = heard[2][1]
    assert pushed["origin"] == "tui-1" and pushed["state"]["totals"]["turns"] == 1
    assert replies[0].json()["state"] == pushed["state"]  # the reply's snapshot IS the pushed one


def test_a_streamed_turn_pushes_the_same_face_event(tmp_path):
    client, _ = _client(tmp_path)
    [heard] = _listen(client, lambda: client.post("/v1/turn/stream", json={"text": "?", "turn_id": "s-1"},
                                                  headers=ME))
    assert [d for e, d in heard if e == "turn"] == [
        {"emotion": "joy", "intensity": 0.8, "theme": heard[1][1]["theme"], "origin": "tui-1"}]


def test_a_request_without_a_client_id_pushes_origin_null(tmp_path):
    client, _ = _client(tmp_path)
    [heard] = _listen(client, lambda: client.post("/v1/turn", json={"text": "?"}, headers=AUTH))
    assert all(d["origin"] is None for _, d in heard)


def test_state_is_pushed_only_on_change(tmp_path):
    client, core = _client(tmp_path)
    style = "warm"

    def act():
        client.post("/v1/command", json={"line": "/closeness"}, headers=ME)  # reads only — nothing to push
        client.post("/v1/command", json={"line": f"/style {style}"}, headers=ME)  # a recommendation — pushed

    [heard] = _listen(client, act)
    assert [e for e, _ in heard] == ["state", "state"]
    assert heard[1][1]["origin"] == "tui-1" and style in heard[1][1]["state"]["style"]


def test_a_new_session_pushes_its_id(tmp_path):
    client, _ = _client(tmp_path)
    before = client.get("/v1/state", headers=AUTH).json()["session_id"]
    [heard] = _listen(client, lambda: client.post("/v1/session/new", headers=ME))
    assert heard[-1][1]["state"]["session_id"] not in (None, before)


def test_a_failed_turn_pushes_no_face_and_frees_the_lock(tmp_path):
    def boom(system, messages, model):
        raise LLMError("model down")

    client, _ = _client(tmp_path, MockLLMClient(boom))
    [heard] = _listen(client, lambda: client.post("/v1/turn", json={"text": "?"}, headers=ME))
    assert "turn" not in [e for e, _ in heard]
    assert client.post("/v1/command", json={"line": "/closeness"}, headers=AUTH).status_code == 200


def test_every_listener_hears_the_turn(tmp_path):
    client, _ = _client(tmp_path)
    heard = _listen(client, lambda: client.post("/v1/turn", json={"text": "?"}, headers=ME), listeners=2)
    assert [e for e, _ in heard[0]] == [e for e, _ in heard[1]] == ["state", "turn", "state"]


def test_mid_turn_the_state_is_the_last_consistent_snapshot(tmp_path):
    """v2.2 review #7: a status read while a turn runs never sees a half-updated core."""
    client, core = _client(tmp_path)
    service = client.app.state.service
    before = client.get("/v1/state", headers=AUTH).json()
    style = "warm"
    service.lock.acquire()  # a turn is inside the core…
    try:
        core.set_style(style)  # …moving its state
        assert client.get("/v1/state", headers=AUTH).json() == before  # not the half-way core
    finally:
        service.lock.release()
    assert style in client.get("/v1/state", headers=AUTH).json()["style"]  # idle again: fresh


def test_a_background_change_is_pushed_after_the_turn_in_flight(tmp_path):
    """The startup mood lands in the background — ``refresh_state`` waits for a running turn, then pushes."""
    client, core = _client(tmp_path)
    service = client.app.state.service
    sub = service.bus.subscribe()
    service.refresh_state()
    assert sub.empty()  # nothing changed — nothing pushed
    service.lock.acquire()
    core.set_style("warm")
    t = threading.Thread(target=service.refresh_state)
    t.start()
    time.sleep(0.1)
    assert sub.empty()  # still waiting for the turn
    service.lock.release()
    t.join(2)
    event, data = sub.get(timeout=1)
    assert event == "state" and data["origin"] is None


def test_the_events_route_needs_the_token(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/v1/events").status_code == 401


def test_a_listening_client_never_holds_the_graceful_stop_open(tmp_path):
    """Real uvicorn on an ephemeral port: the first Ctrl+C ends the events stream, so the stop completes
    instead of waiting forever for the listener to leave."""
    from server.__main__ import make_server

    client, _ = _client(tmp_path)
    server = make_server(client.app, host="127.0.0.1", port=0)
    serving = threading.Thread(target=server.run, daemon=True)
    serving.start()
    _wait(lambda: server.started)
    port = server.servers[0].sockets[0].getsockname()[1]
    heard: dict = {}

    def listen():
        with httpx.stream("GET", f"http://127.0.0.1:{port}/v1/events", headers=AUTH, timeout=10) as res:
            for line in res.iter_lines():
                if line.startswith("event:"):
                    heard.setdefault("first", line)
        heard["ended"] = True

    listener = threading.Thread(target=listen, daemon=True)
    listener.start()
    _wait(lambda: "first" in heard)
    server.handle_exit(signal.SIGINT, None)  # the first Ctrl+C
    serving.join(5)
    assert not serving.is_alive()  # stopped although a client was listening
    listener.join(2)
    assert heard == {"first": "event: state", "ended": True}

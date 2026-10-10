"""Contract: the server API v1 (LUMI-212) — the route set, the token rule, the payload shapes.

Every client (the TUI client mode, the CLI, the pywebview client in v2.8) depends on exactly this. A
change here is a contract change: ARCHITECTURE §Contracts and this test move together.
"""

import json

from fastapi.testclient import TestClient

from core.agent import Core
from core.emotion import Emotion
from core.llm import MockLLMClient
from server.app import create_app
from state.local_store import JsonRepository

TOKEN = "contract-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _client(tmp_path):
    core = Core(llm=MockLLMClient(states={"reply": "так", "emotion": "calm", "intensity": 0.5}),
                repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m")
    return TestClient(create_app(core, token=TOKEN, env="dev", version="2.2.0"))


def test_the_route_set_is_pinned(tmp_path):
    routes = {(m, r.path) for r in _client(tmp_path).app.routes if r.path.startswith("/v1/") for m in r.methods}
    assert routes == {
        ("GET", "/v1/health"), ("GET", "/v1/state"), ("POST", "/v1/turn"),
        ("POST", "/v1/command"), ("POST", "/v1/session/new"),
        ("POST", "/v1/turn/stream"),  # v2.3: the streamed turn (SSE)
        ("GET", "/v1/events"),  # v2.4: the push channel (SSE)
    }


def test_no_public_docs_or_schema(tmp_path):
    client = _client(tmp_path)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_only_health_is_open(tmp_path):
    client = _client(tmp_path)
    assert client.get("/v1/health").status_code == 200
    assert client.get("/v1/state").status_code == 401


def test_turn_carries_the_emotion_contract(tmp_path):
    res = _client(tmp_path).post("/v1/turn", json={"text": "?"}, headers=AUTH).json()
    assert set(res) == {"reply", "emotion", "intensity", "thinking", "intent", "style", "stats", "state"}
    assert isinstance(res["reply"], str) and res["reply"]
    assert res["emotion"] in {e.value for e in Emotion}
    assert 0.0 <= res["intensity"] <= 1.0


def test_state_shape_is_pinned(tmp_path):
    state = _client(tmp_path).get("/v1/state", headers=AUTH).json()
    assert set(state) == {
        "env", "version", "session_id", "model", "provider", "profile", "thinking", "style",
        "last_emotion", "last_intent", "last_thinking", "last_ttft_ms", "last_stats", "totals",
        "mood", "theme", "think_show",
        "stream_enabled",  # v2.3
        "closeness",  # v2.4
    }
    assert set(state["closeness"]) == {"level", "name"}  # the level by name — never the raw value
    assert set(state["totals"]) == {
        "turns", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "latency_ms",
    }


def test_command_shape_is_pinned(tmp_path):
    res = _client(tmp_path).post("/v1/command", json={"line": "/mood"}, headers=AUTH).json()
    assert set(res) == {"handled", "result", "state"}
    assert set(res["result"]) == {"text", "kind", "confirm", "turn"}



def _events(raw: str) -> list[tuple[str, dict]]:
    out = []
    for frame in raw.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines() if not line.startswith(":"))
        if "event" in lines:  # a ':' heartbeat comment carries no event
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_the_stream_event_set_and_done_shape(tmp_path):
    client = _client(tmp_path)
    res = client.post("/v1/turn/stream", json={"text": "?", "turn_id": "c-1"}, headers=AUTH)
    assert res.headers["content-type"].startswith("text/event-stream")
    events = _events(res.text)
    assert {e for e, _ in events} <= {"delta", "think", "done", "error"}
    assert events[-1][0] == "done" and [e for e, _ in events].count("done") == 1  # exactly one outcome
    done = events[-1][1]
    assert set(done) == {"reply", "emotion", "intensity", "thinking", "intent", "style", "stats", "state"}


def test_a_known_turn_id_never_runs_twice(tmp_path):
    client = _client(tmp_path)
    core = client.app.state.service.core
    first = client.post("/v1/turn", json={"text": "привіт", "turn_id": "c-2"}, headers=AUTH).json()
    again = client.post("/v1/turn", json={"text": "привіт", "turn_id": "c-2"}, headers=AUTH).json()
    assert again["reply"] == first["reply"]
    assert core.totals.turns == 1  # the retry returned the stored result — no second turn


def test_the_stream_route_needs_the_token(tmp_path):
    assert _client(tmp_path).post("/v1/turn/stream", json={"text": "?"}).status_code == 401


# --- v2.4: the push channel -------------------------------------------------------------------------------

def _heard(client, action) -> list[tuple[str, dict]]:
    import threading
    import time

    bus = client.app.state.service.bus
    got: dict = {}
    t = threading.Thread(target=lambda: got.update(res=client.get("/v1/events", headers=AUTH)))
    t.start()
    deadline = time.monotonic() + 5
    while bus.listeners == 0:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    action()
    bus.close()  # ends the stream (the server's shutdown does the same)
    t.join(5)
    return _events(got["res"].text)


def test_the_push_event_set_and_shapes(tmp_path):
    client = _client(tmp_path)
    heard = _heard(client, lambda: client.post("/v1/turn", json={"text": "?"},
                                               headers={**AUTH, "X-Lumi-Client": "c-9"}))
    assert {e for e, _ in heard} <= {"state", "turn", "thought"}
    assert heard[0] == ("state", {"origin": None, "state": heard[0][1]["state"]})  # the listener's start
    turn = next(d for e, d in heard if e == "turn")
    assert set(turn) == {"emotion", "intensity", "theme", "origin"}  # the face signal — nothing more
    assert turn["emotion"] in {e.value for e in Emotion} and 0.0 <= turn["intensity"] <= 1.0
    assert turn["origin"] == "c-9"  # the requester's X-Lumi-Client
    assert all(set(d) == {"origin", "state"} for e, d in heard if e == "state")


def test_no_face_assets_ever_cross_the_api(tmp_path):
    """The face is a client-side concern (local packs): a theme NAME may cross, never a file or a path."""
    client = _client(tmp_path)
    for route in client.app.routes:
        assert "face" not in route.path and "image" not in route.path
    heard = _heard(client, lambda: client.post("/v1/turn", json={"text": "?"}, headers=AUTH))
    raw = json.dumps(heard, ensure_ascii=False)
    for marker in (".png", ".jpg", ".webp", "faces/", "base64"):
        assert marker not in raw
    theme = next(d for e, d in heard if e == "turn")["theme"]
    assert theme is None or ("/" not in theme and "." not in theme)

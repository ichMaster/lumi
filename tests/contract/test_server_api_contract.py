"""Contract: the server API v1 (LUMI-212) — the route set, the token rule, the payload shapes.

Every client (the TUI client mode, the CLI, the pywebview client in v2.8) depends on exactly this. A
change here is a contract change: ARCHITECTURE §Contracts and this test move together.
"""

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
    }
    assert set(state["totals"]) == {
        "turns", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "latency_ms",
    }


def test_command_shape_is_pinned(tmp_path):
    res = _client(tmp_path).post("/v1/command", json={"line": "/mood"}, headers=AUTH).json()
    assert set(res) == {"handled", "result", "state"}
    assert set(res["result"]) == {"text", "kind", "confirm", "turn"}



def _events(raw: str) -> list[tuple[str, dict]]:
    import json

    out = []
    for frame in raw.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines() if ": " in line)
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

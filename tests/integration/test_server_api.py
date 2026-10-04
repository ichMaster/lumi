"""The v2.2 server over a mock-model core (LUMI-212) — FastAPI's TestClient, no sockets, no paid API."""

import base64

import pytest
from fastapi.testclient import TestClient

from core.agent import Core
from core.llm import LLMError, MockLLMClient
from server.app import create_app
from state.local_store import JsonRepository

TOKEN = "test-token-not-a-secret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
# A 1×1 transparent PNG — a real image block for the /image-over-the-API path.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def _core(tmp_path, llm=None, **kw):
    return Core(
        llm=llm or MockLLMClient(states={"reply": "Радо!", "emotion": "joy", "intensity": 0.8}),
        repository=JsonRepository(tmp_path / "s.json"), canon="Ти — Лілі.",
        model="claude-haiku-4-5-20251001", **kw,
    )


def _client(tmp_path, **kw):
    core = _core(tmp_path, **kw)
    return TestClient(create_app(core, token=TOKEN, env="dev", version="2.2.0")), core


def test_health_needs_no_token(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/v1/health").json() == {"ok": True, "env": "dev", "version": "2.2.0"}


@pytest.mark.parametrize(("method", "path", "body"), [
    ("get", "/v1/state", None),
    ("post", "/v1/turn", {"text": "привіт"}),
    ("post", "/v1/command", {"line": "/mood"}),
    ("post", "/v1/session/new", None),
])
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}])
def test_every_guarded_route_rejects_a_missing_or_wrong_token(tmp_path, method, path, body, headers):
    client, _ = _client(tmp_path)
    res = getattr(client, method)(path, headers=headers, **({"json": body} if body else {}))
    assert res.status_code == 401
    assert TOKEN not in res.text


def test_a_full_turn(tmp_path):
    client, core = _client(tmp_path)
    res = client.post("/v1/turn", json={"text": "привіт"}, headers=AUTH).json()
    assert res["reply"] == "Радо!" and res["emotion"] == "joy" and res["intensity"] == 0.8
    assert res["state"]["last_emotion"] == {"emotion": "joy", "intensity": 0.8}
    assert res["state"]["totals"]["turns"] == 1
    assert TOKEN not in str(res)


def test_a_malformed_reply_still_degrades_through_the_gate(tmp_path):
    llm = MockLLMClient(states={"reply": "ок", "emotion": "ecstatic", "intensity": 9})
    client, _ = _client(tmp_path, llm=llm)
    res = client.post("/v1/turn", json={"text": "?"}, headers=AUTH).json()
    assert res["emotion"] == "calm" and res["intensity"] == 1.0  # unknown → calm, clamped


def test_an_image_turn(tmp_path):
    from core.images import image_block

    img = tmp_path / "x.png"
    img.write_bytes(_PNG)
    client, _ = _client(tmp_path, image_enabled=True)
    res = client.post("/v1/turn", json={"text": "що тут?", "images": [image_block(img)]}, headers=AUTH)
    assert res.status_code == 200 and res.json()["reply"] == "Радо!"


def test_a_model_failure_is_a_readable_502(tmp_path):
    def boom(system, messages, model):
        raise LLMError("model down")

    client, _ = _client(tmp_path, llm=MockLLMClient(boom))
    res = client.post("/v1/turn", json={"text": "?"}, headers=AUTH)
    assert res.status_code == 502 and res.json() == {"detail": "model unavailable: model down"}
    ok = client.get("/v1/state", headers=AUTH)  # the server is still alive
    assert ok.status_code == 200


def test_any_other_failure_is_a_readable_500(tmp_path):
    def broken(system, messages, model):
        raise RuntimeError("bug")

    client, _ = _client(tmp_path, llm=MockLLMClient(broken))
    res = client.post("/v1/turn", json={"text": "?"}, headers=AUTH)
    assert res.status_code == 500 and res.json() == {"detail": "turn failed — see the server log"}
    assert "bug" not in res.text  # internals stay in the server log, not on the wire


def test_commands_over_the_api(tmp_path):
    client, _ = _client(tmp_path)
    mood = client.post("/v1/command", json={"line": "/mood"}, headers=AUTH).json()
    assert mood["handled"] is True and mood["result"]["kind"] == "info"
    assert client.post("/v1/command", json={"line": "/nope"}, headers=AUTH).json()["handled"] is False
    model = client.post("/v1/command", json={"line": "/model"}, headers=AUTH).json()
    assert "**Двигун:**" in model["result"]["text"]


def test_the_confirm_protocol_over_the_api(tmp_path):
    client, core = _client(tmp_path)
    client.post("/v1/turn", json={"text": "Мене звати Віталій."}, headers=AUTH)
    asked = client.post("/v1/command", json={"line": "/forget"}, headers=AUTH).json()["result"]
    assert asked["confirm"] and asked["kind"] == "notice"
    done = client.post("/v1/command", json={"line": "/forget", "confirmed": True}, headers=AUTH).json()["result"]
    assert done["kind"] == "ack"


def test_a_new_session(tmp_path):
    client, core = _client(tmp_path)
    first = client.get("/v1/state", headers=AUTH).json()["session_id"]
    client.post("/v1/turn", json={"text": "привіт"}, headers=AUTH)
    res = client.post("/v1/session/new", headers=AUTH).json()
    assert res["ok"] is True and res["state"]["session_id"] != first


def test_a_concurrent_request_is_409(tmp_path):
    client, _ = _client(tmp_path)
    service = client.app.state.service
    service.lock.acquire()  # a turn is "running"
    try:
        res = client.post("/v1/turn", json={"text": "привіт"}, headers=AUTH)
        assert res.status_code == 409 and res.json()["detail"] == "busy"
    finally:
        service.lock.release()


def test_no_token_no_server(tmp_path):
    with pytest.raises(ValueError):
        create_app(_core(tmp_path), token="")

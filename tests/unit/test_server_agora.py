"""v3.1 LUMI-281 — the server's agora space: the routes, the scoped room token, the statuses (fake models only)."""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from core.llm import LLMError
from server.__main__ import agora_problem
from server.app import create_app
from tests._agora_world import (
    OWNER,
    OWNER_AUTH,
    ROOM,
    ROOM_AUTH,
    FakeRoomModel,
    agora_client,
    make_core,
    make_space,
)

TURN = {"channel": "agora", "speaker": "Бруно", "register": "voice", "turn_id": "$trig",
        "text": "Лілі, а ти що думаєш про туман?"}
LINES = {"channel": "agora", "lines": [
    {"id": "$a", "speaker": "Ада", "text": "Доброго ранку всім!", "ts": "2026-11-16T08:01:12+02:00"},
    {"id": "$b", "speaker": "Ich", "text": "Я бачив туман.", "ts": "2026-11-16T08:02:40+02:00"},
]}


def test_health_names_the_api_only_while_the_space_is_on(tmp_path):
    on = agora_client(tmp_path).get("/v1/health").json()
    assert on == {"ok": True, "env": "dev", "version": "3.1.0", "agora": 1}
    off = TestClient(create_app(make_core(tmp_path / "off"), token=OWNER, env="dev", version="3.1.0"))
    assert "agora" not in off.get("/v1/health").json()


def test_observe_stores_and_counts_the_known(tmp_path):
    client = agora_client(tmp_path)
    assert client.post("/v1/observe", json=LINES, headers=ROOM_AUTH).json() == {"stored": 2, "known": 0}
    assert client.post("/v1/observe", json=LINES, headers=ROOM_AUTH).json() == {"stored": 0, "known": 2}


def test_a_room_turn_answers_in_the_contracts_shape(tmp_path):
    client = agora_client(tmp_path)
    res = client.post("/v1/turn", json=TURN, headers=ROOM_AUTH)
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"outcome", "reply", "emotion", "intensity", "turn_id", "stats"}
    assert body["outcome"] == "reply" and body["turn_id"] == "$trig" and body["emotion"] == "thoughtful"
    assert set(body["stats"]) == {"model", "input_tokens", "output_tokens", "latency_ms"}


def test_a_retried_room_turn_never_answers_twice(tmp_path):
    room = FakeRoomModel()
    client = agora_client(tmp_path, room_model=room)
    first = client.post("/v1/turn", json=TURN, headers=ROOM_AUTH).json()
    assert client.post("/v1/turn", json=TURN, headers=ROOM_AUTH).json() == first and room.calls == 1


@pytest.mark.parametrize("method, path, body", [
    ("get", "/v1/state", None), ("get", "/v1/events", None), ("post", "/v1/command", {"line": "/mood"}),
    ("post", "/v1/directive", {"line": "%think"}), ("post", "/v1/session/new", None),
    ("post", "/v1/turn/stream", {"text": "привіт"}), ("post", "/v1/turn", {"text": "привіт"}),
    ("post", "/v1/turn", {"text": "привіт", "channel": "owner"}),
])
def test_the_rooms_token_reaches_nothing_of_his(tmp_path, method, path, body):
    client = agora_client(tmp_path)
    res = getattr(client, method)(path, headers=ROOM_AUTH, **({"json": body} if body is not None else {}))
    assert res.status_code == 403 and res.json()["detail"] == "the room's token reaches only the agora space"


def test_no_token_or_a_wrong_one_is_401(tmp_path):
    client = agora_client(tmp_path)
    assert client.post("/v1/observe", json=LINES).status_code == 401
    assert client.post("/v1/turn", json=TURN, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_the_owners_token_keeps_its_full_reach(tmp_path):
    client = agora_client(tmp_path)
    assert client.post("/v1/observe", json=LINES, headers=OWNER_AUTH).status_code == 200
    assert client.post("/v1/turn", json=TURN, headers=OWNER_AUTH).json()["outcome"] == "reply"
    assert client.get("/v1/state", headers=OWNER_AUTH).status_code == 200
    assert client.post("/v1/turn", json={"text": "привіт"}, headers=OWNER_AUTH).json()["reply"] == "так"


def test_a_second_room_turn_while_one_runs_is_409(tmp_path):
    hold = threading.Event()
    room = FakeRoomModel(hold=hold)
    client = agora_client(tmp_path, room_model=room)
    results: list = []
    runner = threading.Thread(target=lambda: results.append(client.post("/v1/turn", json=TURN, headers=ROOM_AUTH)))
    runner.start()
    assert room.entered.wait(5)
    busy = client.post("/v1/turn", json={**TURN, "turn_id": "$other"}, headers=ROOM_AUTH)
    assert busy.status_code == 409 and busy.json()["detail"] == "Лілі is answering the room — try again"
    hold.set()
    runner.join(10)
    assert results[0].status_code == 200


def test_past_the_budget_is_504_and_no_reply_is_kept(tmp_path):
    hold = threading.Event()
    room = FakeRoomModel(hold=hold)
    space = make_space(tmp_path, room, timeout_s=0.05)
    client = agora_client(tmp_path, space=space)
    import server.app as app_mod

    grace, app_mod.ROOM_GRACE_S = app_mod.ROOM_GRACE_S, 0.05
    try:
        res = client.post("/v1/turn", json=TURN, headers=ROOM_AUTH)
    finally:
        app_mod.ROOM_GRACE_S = grace
        hold.set()
    assert res.status_code == 504 and res.json()["detail"] == "the room's turn timed out"
    for _ in range(100):  # the abandoned turn ends on its own thread
        if not space._turn_lock.locked():
            break
        threading.Event().wait(0.02)
    assert space.store.get("agora", "$trig", "lili") is None


def test_a_model_failure_is_502(tmp_path):
    client = agora_client(tmp_path, room_model=FakeRoomModel(error=LLMError("down")))
    res = client.post("/v1/turn", json=TURN, headers=ROOM_AUTH)
    assert res.status_code == 502 and res.json()["detail"] == "model unavailable"


def test_an_empty_reply_is_a_500_in_v3_1(tmp_path):
    client = agora_client(tmp_path, room_model=FakeRoomModel({"reply": ""}))
    assert client.post("/v1/turn", json=TURN, headers=ROOM_AUTH).status_code == 500


@pytest.mark.parametrize("change", [
    {"speaker": "Ада: Бруно"}, {"speaker": ""}, {"speaker": "x" * 65}, {"speaker": "Ада\nБруно"}, {"speaker": None},
    {"turn_id": ""}, {"turn_id": None}, {"channel": "telegram"}, {"text": "  "},
    {"images": [{"type": "image", "data": "x"}]},
])
def test_a_malformed_room_turn_is_422(tmp_path, change):
    client = agora_client(tmp_path)
    assert client.post("/v1/turn", json={**TURN, **change}, headers=ROOM_AUTH).status_code == 422


@pytest.mark.parametrize("lines", [
    [{"speaker": "Ада", "text": "x", "ts": "2026-11-16T08:01:12+02:00"}],  # no id
    [{"id": "", "speaker": "Ада", "text": "x", "ts": "2026-11-16T08:01:12+02:00"}],
    [{"id": "$a", "speaker": "Ада:", "text": "x", "ts": "2026-11-16T08:01:12+02:00"}],
    [{"id": "$a", "speaker": "Ада", "text": "x", "ts": "вчора"}],
    [{"id": "$a", "speaker": "Ада", "text": "x", "ts": "2026-11-16T08:01:12"}],  # no offset (code review #5)
    [{"id": f"${i}", "speaker": "Ада", "text": "x", "ts": "2026-11-16T08:01:12+02:00"} for i in range(201)],
])
def test_a_malformed_observe_is_422(tmp_path, lines):
    client = agora_client(tmp_path)
    assert client.post("/v1/observe", json={"channel": "agora", "lines": lines}, headers=ROOM_AUTH).status_code == 422
    assert client.post("/v1/observe", json={"lines": LINES["lines"]}, headers=ROOM_AUTH).status_code == 422


def test_200_lines_is_the_cap_not_a_refusal(tmp_path):
    lines = [{"id": f"${i}", "speaker": "Ада", "text": "x", "ts": "2026-11-16T08:01:12+02:00"} for i in range(200)]
    res = agora_client(tmp_path).post("/v1/observe", json={"channel": "agora", "lines": lines}, headers=ROOM_AUTH)
    assert res.json() == {"stored": 200, "known": 0}


def test_the_room_stream_is_refused(tmp_path):
    res = agora_client(tmp_path).post("/v1/turn/stream", json=TURN, headers=OWNER_AUTH)
    assert res.status_code == 422


def test_with_the_space_off_the_room_is_absent(tmp_path):
    client = TestClient(create_app(make_core(tmp_path), token=OWNER, env="dev", version="3.1.0"))
    assert client.post("/v1/observe", json=LINES, headers=OWNER_AUTH).status_code == 404
    assert client.post("/v1/turn", json=TURN, headers=OWNER_AUTH).status_code == 404
    assert client.post("/v1/turn", json=TURN, headers={"Authorization": f"Bearer {ROOM}"}).status_code == 401


def test_the_space_needs_its_own_token(tmp_path):
    with pytest.raises(ValueError):
        create_app(make_core(tmp_path), token=OWNER, space=make_space(tmp_path))
    with pytest.raises(ValueError):
        create_app(make_core(tmp_path), token=OWNER, space=make_space(tmp_path), room_token=OWNER)


class _Cfg:
    def __init__(self, **kw):
        self.agora, self.agora_token, self.server_token, self.gemini_api_key = False, "", "owner", "g"
        self.__dict__.update(kw)


def test_the_server_refuses_a_space_without_its_token_or_model():
    assert agora_problem(_Cfg()) is None  # off
    assert agora_problem(_Cfg(agora=True, agora_token="room")) is None
    assert "agora.token" in agora_problem(_Cfg(agora=True))
    assert "client token" in agora_problem(_Cfg(agora=True, agora_token="owner"))
    assert "GEMINI_API_KEY" in agora_problem(_Cfg(agora=True, agora_token="room", gemini_api_key=""))

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
    assert on == {"ok": True, "env": "dev", "version": "3.1.0", "agora": 2}  # v3.4: the API v2 (her initiative)
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


def test_an_empty_reply_is_a_pass(tmp_path):
    # v3.3: silence, a 200 — was a 500 in v3.1
    client = agora_client(tmp_path, room_model=FakeRoomModel({"reply": ""}))
    res = client.post("/v1/turn", json=TURN, headers=ROOM_AUTH)
    assert res.status_code == 200 and res.json()["outcome"] == "pass" and res.json()["reply"] == ""


def test_the_register_is_voice_or_none(tmp_path):
    client = agora_client(tmp_path)
    assert client.post("/v1/turn", json={**TURN, "register": None}, headers=ROOM_AUTH).status_code == 200
    assert client.post("/v1/turn", json={**TURN, "turn_id": "$v", "register": "voice"},
                       headers=ROOM_AUTH).status_code == 200
    assert client.post("/v1/turn", json={**TURN, "turn_id": "$t", "register": "thinking"},
                       headers=ROOM_AUTH).status_code == 422


def test_his_channel_still_errs_on_an_empty_reply(tmp_path):
    from core.llm import MockLLMClient

    core = make_core(tmp_path, llm=MockLLMClient(states={"reply": ""}))
    client = agora_client(tmp_path, core=core)
    assert client.post("/v1/turn", json={"text": "привіт"}, headers=OWNER_AUTH).status_code == 500


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


# --- v3.3 LUMI-290: retract over the API ---------------------------------------------------------------------------


def test_retract_is_always_a_200_and_idempotent(tmp_path):
    client = agora_client(tmp_path)
    client.post("/v1/turn", json=TURN, headers=ROOM_AUTH)
    assert client.post("/v1/turn/%24trig/retract", headers=ROOM_AUTH).json() == {"retracted": True}
    assert client.post("/v1/turn/%24trig/retract", headers=ROOM_AUTH).json() == {"retracted": False}
    assert client.post("/v1/turn/%24nobody/retract", headers=ROOM_AUTH).json() == {"retracted": False}


def test_retract_is_the_rooms_own_route(tmp_path):
    client = agora_client(tmp_path)
    res = client.post("/v1/turn/%24trig/retract", headers=OWNER_AUTH)
    assert res.status_code == 403 and res.json()["detail"] == "retract is the room's own route"
    assert client.post("/v1/turn/%24trig/retract").status_code == 401
    off = TestClient(create_app(make_core(tmp_path / "off"), token=OWNER, env="dev", version="3.3.0"))
    assert off.post("/v1/turn/%24trig/retract", headers=OWNER_AUTH).status_code == 404  # the space off


def test_the_hourly_cap_is_a_429(tmp_path):
    client = agora_client(tmp_path, space=make_space(tmp_path, max_turns_per_hour=1))
    assert client.post("/v1/turn", json=TURN, headers=ROOM_AUTH).status_code == 200
    capped = client.post("/v1/turn", json={**TURN, "turn_id": "$second"}, headers=ROOM_AUTH)
    assert capped.status_code == 429 and capped.json()["detail"] == "the room's hourly cap is reached"


# --- v3.4 LUMI-299: her initiative's routes -------------------------------------------------------------------------

INI_TEXT = "Сьогодні вітер уже пахне першим снігом. У вас теж так?"


def _ini_client(tmp_path, *, initiative=True, model=None):
    model = model or FakeRoomModel({"reply": INI_TEXT, "emotion": "calm", "intensity": 0.4})
    space = make_space(tmp_path, initiative=initiative, initiative_llm=model)
    return agora_client(tmp_path, space=space), space, model


def test_the_initiative_routes_are_the_rooms_own(tmp_path):
    client, space, model = _ini_client(tmp_path)
    assert client.get("/v1/initiative", headers=OWNER_AUTH).status_code == 403
    assert client.post("/v1/initiative/ini-1/posted", json={"event_id": "$p"}, headers=OWNER_AUTH).status_code == 403
    assert client.get("/v1/initiative").status_code == 401
    assert client.post("/v1/initiative/ini-1/posted", json={"event_id": "$p"}).status_code == 401
    assert model.calls == 0 and space._ini_thread is None  # nothing composed for a refused caller


def test_the_space_off_has_no_initiative_routes(tmp_path):
    off = TestClient(create_app(make_core(tmp_path), token=OWNER, env="dev", version="3.4.0"))
    assert off.get("/v1/initiative", headers=OWNER_AUTH).status_code == 404
    assert off.post("/v1/initiative/ini-1/posted", json={"event_id": "$p"}, headers=OWNER_AUTH).status_code == 404


def test_the_initiative_off_is_always_none(tmp_path):
    client, space, model = _ini_client(tmp_path, initiative=False)
    client.post("/v1/observe", json=LINES, headers=ROOM_AUTH)
    assert [client.get("/v1/initiative", headers=ROOM_AUTH).json() for _ in range(3)] == [{"outcome": "none"}] * 3
    assert model.calls == 0 and space._ini_thread is None
    assert client.get("/v1/health").json()["agora"] == 2  # still the v2 API


def test_a_poll_then_an_ack_through_the_server(tmp_path):
    client, space, model = _ini_client(tmp_path)
    client.post("/v1/observe", json=LINES, headers=ROOM_AUTH)
    assert client.get("/v1/initiative", headers=ROOM_AUTH).json() == {"outcome": "none"}
    space._ini_thread.join(5)
    say = client.get("/v1/initiative", headers=ROOM_AUTH).json()
    assert say["outcome"] == "say" and say["text"] == INI_TEXT and model.calls == 1
    path = f"/v1/initiative/{say['initiative_id']}/posted"
    assert client.post(path, json={"event_id": "$posted"}, headers=ROOM_AUTH).json() == {"stored": True}
    assert client.post(path, json={"event_id": "$posted"}, headers=ROOM_AUTH).json() == {"stored": False}
    assert client.post("/v1/initiative/ini-000000000000/posted", json={"event_id": "$x"},
                       headers=ROOM_AUTH).json() == {"stored": False}
    assert client.get("/v1/initiative", headers=ROOM_AUTH).json() == {"outcome": "none"}  # acked → never again


def test_a_failing_poll_is_none(tmp_path, caplog):
    import logging

    client, space, _ = _ini_client(tmp_path)

    def boom():
        raise RuntimeError("the store is gone")

    space.poll_initiative = boom  # type: ignore[method-assign]
    with caplog.at_level(logging.WARNING, logger="lumi.server"):
        res = client.get("/v1/initiative", headers=ROOM_AUTH)
    assert res.status_code == 200 and res.json() == {"outcome": "none"} and "initiative poll failed" in caplog.text


def test_his_turns_and_state_never_see_her_initiative(tmp_path):
    client, space, _ = _ini_client(tmp_path)
    client.post("/v1/observe", json=LINES, headers=ROOM_AUTH)
    before = client.get("/v1/state", headers=OWNER_AUTH).json()
    client.get("/v1/initiative", headers=ROOM_AUTH)
    space._ini_thread.join(5)
    say = client.get("/v1/initiative", headers=ROOM_AUTH).json()
    client.post(f"/v1/initiative/{say['initiative_id']}/posted", json={"event_id": "$posted"}, headers=ROOM_AUTH)
    after = client.get("/v1/state", headers=OWNER_AUTH).json()
    assert INI_TEXT not in str(after) and before.keys() == after.keys()
    his = (tmp_path / "store.json").read_text(encoding="utf-8") if (tmp_path / "store.json").exists() else ""
    assert INI_TEXT not in his  # his store never holds her room line


def test_the_server_wires_the_initiative_from_settings(tmp_path):
    from core.config import Config
    from server.__main__ import agora_space_of

    core = make_core(tmp_path)
    cfg = Config(agora=True, agora_token="room", gemini_api_key="k", agora_initiative=True,
                 agora_store_path=tmp_path / "spaces.sqlite", store_path=tmp_path / "store.json")
    space = agora_space_of(cfg, core)
    try:
        assert space._initiative is True
        assert space._ini_llm is not space._llm and space._ini_llm is not space._hk_llm  # a client of its own
    finally:
        space.close(wait_s=0.1)
    off = agora_space_of(Config(agora=True, agora_token="room", gemini_api_key="k",
                                agora_store_path=tmp_path / "b.sqlite", store_path=tmp_path / "store.json"), core)
    try:
        assert off._initiative is False
    finally:
        off.close(wait_s=0.1)


def test_a_failing_ack_is_a_200_nothing_stored(tmp_path, caplog):
    # v3.4 code review #3: the contract's ack is always a 200 — a store error is {stored: false}, logged
    import logging

    client, space, _ = _ini_client(tmp_path)

    def boom(initiative_id, event_id):
        raise OSError("disk full")

    space.initiative_posted = boom  # type: ignore[method-assign]
    with caplog.at_level(logging.WARNING, logger="lumi.server"):
        res = client.post("/v1/initiative/ini-1/posted", json={"event_id": "$p"}, headers=ROOM_AUTH)
    assert res.status_code == 200 and res.json() == {"stored": False} and "initiative ack failed" in caplog.text

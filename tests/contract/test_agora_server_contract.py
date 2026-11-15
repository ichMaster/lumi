"""Contract (v3.1): the server accepts and returns the agora space API v1 examples as written.

``docs/agora-space-api.json`` is the seam with matrix-agora (copied there as ``tests/fixtures/lumi_agora_api_v1.json``,
its fake Lumi server built from the same examples). This drives each example request through the real app — a fake
room model that returns the example's reply — and checks the response against the example: its status, its fields
and, where Lumi decides them, its values. v3.3 adds ``turn_pass`` and ``retract``.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.llm import ResponseStats
from tests._agora_world import ROOM, FakeRoomModel, agora_client

API = json.loads((Path(__file__).resolve().parents[2] / "docs" / "agora-space-api.json").read_text(encoding="utf-8"))
EP = API["endpoints"]
AUTH = {"Authorization": f"Bearer {ROOM}", API["auth"]["client_header"].split(":")[0]: "agora"}


class ExampleModel(FakeRoomModel):
    """Answers with the turn example's reply and reports the example's token counts."""

    def reply_structured(self, system, messages, model, cache_prefix=None, **_):
        out = super().reply_structured(system, messages, model)
        stats = EP["turn"]["response"]["body"]["stats"]
        self.last_stats = ResponseStats(model=model, latency_ms=stats["latency_ms"],
                                        input_tokens=stats["input_tokens"], output_tokens=stats["output_tokens"])
        return out


def test_health_as_written(tmp_path):
    ex = EP["health"]
    res = agora_client(tmp_path).request(ex["request"]["method"], ex["request"]["path"])
    assert res.status_code == ex["response"]["status"]
    assert res.json() == ex["response"]["body"]  # dev, 3.1.0, agora 1 — the example is v3.1's own health


def test_observe_as_written(tmp_path):
    ex = EP["observe"]
    client = agora_client(tmp_path)
    res = client.request(ex["request"]["method"], ex["request"]["path"], json=ex["request"]["body"], headers=AUTH)
    assert res.status_code == ex["response"]["status"] and res.json() == ex["response"]["body"]


def test_turn_as_written(tmp_path):
    ex = EP["turn"]
    state = {k: ex["response"]["body"][k] for k in ("reply", "emotion", "intensity")}
    client = agora_client(tmp_path, room_model=ExampleModel(state))
    res = client.request(ex["request"]["method"], ex["request"]["path"], json=ex["request"]["body"], headers=AUTH)
    assert res.status_code == ex["response"]["status"]
    assert res.json() == ex["response"]["body"]  # outcome, reply, emotion, intensity, turn_id, stats — as written


def test_the_turn_after_observe_reads_the_observed_lines(tmp_path):
    room = ExampleModel({k: EP["turn"]["response"]["body"][k] for k in ("reply", "emotion", "intensity")})
    client = agora_client(tmp_path, room_model=room)
    client.post(EP["observe"]["request"]["path"], json=EP["observe"]["request"]["body"], headers=AUTH)
    client.post(EP["turn"]["request"]["path"], json=EP["turn"]["request"]["body"], headers=AUTH)
    assert room.calls == 1  # Q6: the bridge flushes observe first; her prompt is built from those lines


def test_the_errors_lumi_returns_in_v3_1_carry_the_contracts_details(tmp_path):
    errors = {e["status"]: e for e in API["errors"]}
    client = agora_client(tmp_path)
    no_token = client.post("/v1/turn", json=EP["turn"]["request"]["body"])
    assert no_token.status_code == 401 and no_token.json() == errors[401]["body"]
    other_route = client.get("/v1/state", headers=AUTH)
    assert other_route.status_code == 403 and other_route.json() == errors[403]["body"]
    other_channel = client.post("/v1/turn", json={"text": "привіт"}, headers=AUTH)
    assert other_channel.status_code == 403 and other_channel.json() == errors[403]["body"]


def test_the_error_details_lumi_returns_match_the_contract(tmp_path):
    import threading

    from core.llm import LLMError
    from tests._agora_world import make_space

    errors = {e["status"]: e["body"]["detail"] for e in API["errors"]}
    model_down = agora_client(tmp_path / "a", room_model=FakeRoomModel(error=LLMError("x")))
    assert model_down.post("/v1/turn", json=EP["turn"]["request"]["body"], headers=AUTH).json()["detail"] == errors[502]
    hold = threading.Event()
    slow = FakeRoomModel(hold=hold)
    client = agora_client(tmp_path / "b", space=make_space(tmp_path / "b", slow))
    results: list = []
    runner = threading.Thread(target=lambda: results.append(
        client.post("/v1/turn", json=EP["turn"]["request"]["body"], headers=AUTH)))
    runner.start()
    assert slow.entered.wait(5)
    busy = client.post("/v1/turn", json=EP["turn_pass"]["request"]["body"], headers=AUTH)
    hold.set()
    runner.join(10)
    assert busy.status_code == 409 and busy.json()["detail"] == errors[409]


def test_turn_pass_as_written(tmp_path):
    # v3.2: the name guard — a reply naming him leaves Lumi as the contract's pass
    from tests._agora_world import make_space

    ex = EP["turn_pass"]
    room = ExampleModel({"reply": "Віталій мені все розповідає.", "emotion": "tender", "intensity": 0.6})
    client = agora_client(tmp_path, space=make_space(tmp_path, room, private_names=["Віталій"]))
    res = client.request(ex["request"]["method"], ex["request"]["path"], json=ex["request"]["body"], headers=AUTH)
    assert res.status_code == ex["response"]["status"]
    body = res.json()
    expected = ex["response"]["body"]
    assert set(body) == set(expected) and body["outcome"] == "pass" and body["reply"] == ""
    assert body["turn_id"] == expected["turn_id"] and set(body["stats"]) == set(expected["stats"])


def test_retract_as_written(tmp_path):
    # v3.3: a stored reply → the example's {retracted: true}; nothing stored → response_nothing_stored (R8)
    ex = EP["retract"]
    client = agora_client(tmp_path)
    turn = {**EP["turn"]["request"]["body"]}
    client.post(EP["turn"]["request"]["path"], json=turn, headers=AUTH)
    path = ex["request"]["example_path"]
    assert path == "/v1/turn/" + turn["turn_id"].replace("$", "%24") + "/retract"
    res = client.request(ex["request"]["method"], path, headers=AUTH)
    assert res.status_code == ex["response"]["status"] and res.json() == ex["response"]["body"]
    again = client.request(ex["request"]["method"], path, headers=AUTH)
    nothing = ex["response_nothing_stored"]
    assert again.status_code == nothing["status"] and again.json() == nothing["body"]


def test_every_error_lumi_returns_carries_the_contracts_detail(tmp_path):
    # v3.3: 404 (the space off), 429 (the cap), 504 (the budget) join 401 / 403 / 409 / 502 above
    import threading

    from fastapi.testclient import TestClient

    from server.app import create_app
    from tests._agora_world import OWNER, make_core, make_space

    errors = {e["status"]: e["body"]["detail"] for e in API["errors"]}
    off = TestClient(create_app(make_core(tmp_path / "off"), token=OWNER))
    res = off.post("/v1/observe", json=EP["observe"]["request"]["body"],
                   headers={"Authorization": f"Bearer {OWNER}"})
    assert res.status_code == 404 and res.json()["detail"] == errors[404]
    capped = agora_client(tmp_path / "cap", space=make_space(tmp_path / "cap", max_turns_per_hour=1))
    capped.post("/v1/turn", json=EP["turn"]["request"]["body"], headers=AUTH)
    res = capped.post("/v1/turn", json=EP["turn_pass"]["request"]["body"], headers=AUTH)
    assert res.status_code == 429 and res.json()["detail"] == errors[429]
    hold = threading.Event()
    slow = make_space(tmp_path / "slow", FakeRoomModel(hold=hold), timeout_s=0.05)
    import server.app as app_mod

    grace, app_mod.ROOM_GRACE_S = app_mod.ROOM_GRACE_S, 0.05
    try:
        res = agora_client(tmp_path / "slow", space=slow).post("/v1/turn", json=EP["turn"]["request"]["body"],
                                                                 headers=AUTH)
    finally:
        app_mod.ROOM_GRACE_S = grace
        hold.set()
    assert res.status_code == 504 and res.json()["detail"] == errors[504]
    bad = agora_client(tmp_path / "bad").post("/v1/observe", json={"channel": "agora", "lines": [{"speaker": "Ада"}]},
                                              headers=AUTH)
    assert bad.status_code == 422  # the contract's 422 detail is a placeholder («…»)


def test_every_endpoint_of_the_contract_is_served(tmp_path):
    client = agora_client(tmp_path)
    served = {(m, r.path) for r in client.app.routes for m in getattr(r, "methods", ())}
    for name, ep in EP.items():
        path = ep["request"]["path"]
        assert (ep["request"]["method"], path) in served, name

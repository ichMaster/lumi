"""v3 (Agora, step 1 of the synchronization with matrix-agora) — the agora space API v1 contract,
``docs/agora-space-api.json``: its version, its endpoints and their examples' shapes. The server checks against the
same examples arrive with v3.1; this pins the file matrix-agora copies as ``tests/fixtures/lumi_agora_api_v1.json``."""

from __future__ import annotations

import json
from pathlib import Path

CONTRACT = Path(__file__).resolve().parents[2] / "docs" / "agora-space-api.json"


def _api() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_the_contract_is_version_1_and_names_its_endpoints():
    api = _api()
    assert api["version"] == 1 and '"version": 1' in CONTRACT.read_text(encoding="utf-8")  # matrix-agora greps it
    assert set(api["endpoints"]) == {"health", "observe", "turn", "turn_pass", "retract"}
    routes = {(e["request"]["method"], e["request"]["path"]) for e in api["endpoints"].values()}
    assert routes == {("GET", "/v1/health"), ("POST", "/v1/observe"), ("POST", "/v1/turn"),
                      ("POST", "/v1/turn/{turn_id}/retract")}


def test_the_examples_carry_the_agreed_fields():
    ep = _api()["endpoints"]
    assert ep["health"]["response"]["body"]["agora"] == 1
    for line in ep["observe"]["request"]["body"]["lines"]:
        assert set(line) == {"id", "speaker", "text", "ts"}
    turn = ep["turn"]["request"]["body"]
    assert turn["channel"] == "agora" and turn["register"] == "voice" and turn["speaker"] and turn["turn_id"]
    reply = ep["turn"]["response"]["body"]
    assert reply["outcome"] == "reply" and reply["reply"] and reply["turn_id"] == turn["turn_id"]
    assert set(reply["stats"]) == {"model", "input_tokens", "output_tokens", "latency_ms"}
    assert not {"thinking", "state", "mood", "intent", "style"} & set(reply)  # never her state in the room
    passed = ep["turn_pass"]["response"]["body"]
    assert passed["outcome"] == "pass" and passed["reply"] == ""


def test_silence_and_errors_are_spelled_out():
    api = _api()
    assert {409, 429, 504} <= set(api["silence"]["statuses"]) and api["silence"]["outcome"] == "pass"
    statuses = {e["status"] for e in api["errors"]}
    assert {401, 403, 404, 409, 422, 429, 502, 504} <= statuses
    assert any("owner" in rule for rule in api["never"])


def test_r1_a_turn_carries_the_trigger_only_and_r5_names_the_cap():
    api = _api()
    for name in ("turn", "turn_pass"):
        body = api["endpoints"][name]["request"]["body"]
        assert "\n" not in body["text"]  # the trigger's own line — earlier lines come from observe, never twice
        assert set(body) == {"channel", "speaker", "register", "turn_id", "text"}
    assert api["limits"]["max_turns_per_hour"] == 20 and api["limits"]["timeout_s"] == 20

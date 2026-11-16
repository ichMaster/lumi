"""v3 (Agora, step 1 of the synchronization with matrix-agora) — the agora space API v1 contract,
``docs/agora-space-api.json``: its version, its endpoints and their examples' shapes. The server checks against the
same examples arrive with v3.1; this pins the file matrix-agora copies as ``tests/fixtures/lumi_agora_api_v1.json``."""

from __future__ import annotations

import json
from pathlib import Path

CONTRACT = Path(__file__).resolve().parents[2] / "docs" / "agora-space-api.json"


def _api() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_the_contract_is_version_2_and_names_its_endpoints():
    # v3.4 (sync step 10): version 2 adds her initiative; every v1 route is unchanged
    api = _api()
    assert api["version"] == 2 and '"version": 2' in CONTRACT.read_text(encoding="utf-8")  # matrix-agora greps it
    assert set(api["endpoints"]) == {"health", "observe", "turn", "turn_pass", "retract",
                                     "initiative", "initiative_none", "initiative_posted"}
    routes = {(e["request"]["method"], e["request"]["path"]) for e in api["endpoints"].values()}
    assert routes == {("GET", "/v1/health"), ("POST", "/v1/observe"), ("POST", "/v1/turn"),
                      ("POST", "/v1/turn/{turn_id}/retract"), ("GET", "/v1/initiative"),
                      ("POST", "/v1/initiative/{initiative_id}/posted")}


def test_the_examples_carry_the_agreed_fields():
    ep = _api()["endpoints"]
    assert ep["health"]["response"]["body"]["agora"] == 2
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
    assert not any("she only answers" in rule for rule in api["never"])  # v2: an offer, never a post
    assert any("an offer, never a post" in rule for rule in api["never"])


def test_r1_a_turn_carries_the_trigger_only_and_r5_names_the_cap():
    api = _api()
    for name in ("turn", "turn_pass"):
        body = api["endpoints"][name]["request"]["body"]
        assert "\n" not in body["text"]  # the trigger's own line — earlier lines come from observe, never twice
        assert set(body) == {"channel", "speaker", "register", "turn_id", "text"}
    assert api["limits"]["max_turns_per_hour"] == 20 and api["limits"]["timeout_s"] == 20


def test_r8_a_retract_is_always_a_200_and_idempotent():
    api = _api()
    retract = api["endpoints"]["retract"]
    assert retract["response"] == {"status": 200, "body": {"retracted": True}}
    assert retract["response_nothing_stored"] == {"status": 200, "body": {"retracted": False}}
    assert "Idempotent" in retract["about"] and "never an error" in retract["about"]
    assert not any("retract of" in e["when"] for e in api["errors"])  # no 404 for an unknown turn_id


def test_q3_the_speaker_rule_is_spelled_out():
    api = _api()
    about = api["endpoints"]["observe"]["about"]
    assert "1–64 characters" in about and "no ':'" in about and "422" in about
    for line in api["endpoints"]["observe"]["request"]["body"]["lines"]:
        assert 1 <= len(line["speaker"]) <= 64 and ":" not in line["speaker"]
    assert any("speaker" in e["when"] for e in api["errors"] if e["status"] == 422)


def test_q9_a_ts_carries_its_offset():
    from datetime import datetime

    api = _api()
    assert "with its offset" in api["endpoints"]["observe"]["about"]
    for line in api["endpoints"]["observe"]["request"]["body"]["lines"]:
        assert datetime.fromisoformat(line["ts"]).tzinfo is not None



def test_v2_her_initiative_is_an_offer_acked_before_it_is_remembered():
    api = _api()
    ep = api["endpoints"]
    say = ep["initiative"]["response"]["body"]
    assert say["outcome"] == "say" and say["text"] and say["initiative_id"].startswith("ini-")
    assert set(say) == {"outcome", "initiative_id", "text", "stats"}
    assert "Ich" not in say["text"] and not {"thinking", "state", "mood"} & set(say)
    assert ep["initiative_none"]["response"]["body"] == {"outcome": "none"}
    posted = ep["initiative_posted"]
    assert set(posted["request"]["body"]) == {"event_id"} and posted["request"]["body"]["event_id"].startswith("$")
    assert posted["response"] == {"status": 200, "body": {"stored": True}}
    assert posted["response_nothing_stored"] == {"status": 200, "body": {"stored": False}}
    assert posted["request"]["example_path"] == "/v1/initiative/" + say["initiative_id"] + "/posted"
    assert "never calls the model" in ep["initiative"]["about"]
    limits = api["limits"]
    assert (limits["initiative_fresh_s"], limits["initiative_compose_gap_s"], limits["initiative_kept_s"]) == (
        1800, 600, 86400)  # v3.4.1: the compose gap 600 s (the owner, 2026-11-17)
    assert any("«Ich»" in rule for rule in api["never"])

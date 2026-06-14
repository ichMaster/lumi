"""v0.18 — the MiniMax backend (stdlib HTTP via an injected transport, no network/paid calls)."""
from __future__ import annotations

import pytest

from core.config import Config
from core.emotion import validate
from core.llm import LLMClient, LLMError, MiniMaxClient, build_llm


def _transport(content: str, *, usage: dict | None = None, base_resp: dict | None = None):
    """A fake MiniMax transport: records the call, returns a canned OpenAI-shaped response."""
    seen: dict = {}

    def call(url: str, headers: dict, body: dict) -> dict:
        seen["url"], seen["headers"], seen["body"] = url, headers, body
        resp: dict = {"choices": [{"message": {"content": content}}]}
        if usage is not None:
            resp["usage"] = usage
        if base_resp is not None:
            resp["base_resp"] = base_resp
        return resp

    call.seen = seen  # type: ignore[attr-defined]
    return call


def _client(content: str, **kw) -> MiniMaxClient:
    return MiniMaxClient("k", _transport=_transport(content, **kw))


def test_satisfies_llmclient_protocol():
    assert isinstance(_client("{}"), LLMClient)


def test_reply_structured_valid_json_through_v03_gate():
    c = _client('{"reply":"вітаю","emotion":"tender","intensity":0.6}')
    state = validate(c.reply_structured("sys", [{"role": "user", "content": "hi"}], "MiniMax-Text-01"))
    assert state.reply == "вітаю" and state.emotion.value == "tender" and state.intensity == 0.6


def test_reply_structured_malformed_degrades_to_calm():
    c = _client("definitely not json")
    state = validate(c.reply_structured("sys", [{"role": "user", "content": "x"}], "m"))
    assert state.emotion.value == "calm" and state.reply == "definitely not json"


def test_transport_gets_bearer_auth_endpoint_and_json_instruction():
    t = _transport('{"reply":"a","emotion":"calm","intensity":0.5}')
    c = MiniMaxClient("secret-key", _transport=t)
    c.reply_structured("SYSTEM", [{"role": "user", "content": "hi"}], "MiniMax-Text-01")
    assert t.seen["url"].endswith("/text/chatcompletion_v2")
    assert t.seen["headers"]["Authorization"] == "Bearer secret-key"
    assert t.seen["body"]["model"] == "MiniMax-Text-01"
    assert "JSON object" in t.seen["body"]["messages"][0]["content"]


def test_base_resp_error_raises_llmerror():
    c = _client("{}", base_resp={"status_code": 1004, "status_msg": "auth failed"})
    with pytest.raises(LLMError, match="MiniMax error 1004"):
        c.reply("sys", [{"role": "user", "content": "hi"}], "m")


def test_last_stats_populated_from_usage():
    c = _client('{"reply":"a","emotion":"calm","intensity":0.5}',
                usage={"prompt_tokens": 90, "completion_tokens": 30})
    c.reply_structured("sys", [{"role": "user", "content": "hi"}], "abab6.5s-chat")
    assert c.last_stats.input_tokens == 90 and c.last_stats.output_tokens == 30
    assert c.last_stats.model == "abab6.5s-chat" and c.last_thinking is None


def test_network_error_wrapped_as_llmerror():
    def boom(url, headers, body):
        raise RuntimeError("connection refused")

    c = MiniMaxClient("k", _transport=boom, retries=0)
    with pytest.raises(LLMError, match="MiniMax call failed"):
        c.reply("sys", [{"role": "user", "content": "hi"}], "m")


def test_factory_builds_minimax_and_checks_key():
    assert isinstance(build_llm(Config(provider="minimax", minimax_api_key="mk")), MiniMaxClient)
    with pytest.raises(LLMError, match="MINIMAX_API_KEY"):
        build_llm(Config(provider="minimax"))

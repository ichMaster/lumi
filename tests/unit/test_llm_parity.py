"""v0.18 — cross-provider parity: the shared v0.3 gate + Anthropic-only feature degradation + stats."""
from __future__ import annotations

import logging
from types import SimpleNamespace

from core.closeness import validate_relation
from core.config import Config
from core.emotion import validate
from core.llm import (
    GeminiClient,
    MiniMaxClient,
    MockLLMClient,
    OpenAICompatibleClient,
    build_llm,
)

_VALID = '{"reply":"ок","emotion":"joy","intensity":0.9}'
_MALFORMED = "sorry, not json"


def _openai(content: str, usage: object | None = None) -> OpenAICompatibleClient:
    comp = SimpleNamespace(create=lambda **kw: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=usage))
    return OpenAICompatibleClient("k", _client=SimpleNamespace(chat=SimpleNamespace(completions=comp)))


def _minimax(content: str, usage: dict | None = None) -> MiniMaxClient:
    def transport(url, headers, body):
        resp = {"choices": [{"message": {"content": content}}]}
        if usage is not None:
            resp["usage"] = usage
        return resp

    return MiniMaxClient("k", _transport=transport)


def _gemini(content: str, usage: dict | None = None) -> GeminiClient:
    def transport(url, headers, body):
        resp = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": content}]}}]}
        if usage is not None:
            resp["usageMetadata"] = usage
        return resp

    return GeminiClient("k", _transport=transport)


def _backends_valid():
    return [_openai(_VALID), _minimax(_VALID), _gemini(_VALID),
            MockLLMClient(states={"reply": "ок", "emotion": "joy", "intensity": 0.9})]


def _backends_malformed():
    return [_openai(_MALFORMED), _minimax(_MALFORMED), _gemini(_MALFORMED)]


# --- the shared v0.3 gate is uniform across providers ---------------------------------------------
def test_every_backend_raw_output_passes_the_v03_gate():
    for client in _backends_valid():
        state = validate(client.reply_structured("sys", [{"role": "user", "content": "hi"}], "m"))
        assert state.emotion.value == "joy" and 0.0 <= state.intensity <= 1.0 and state.reply


def test_malformed_output_degrades_to_calm_on_every_provider():
    for client in _backends_malformed():
        state = validate(client.reply_structured("sys", [{"role": "user", "content": "x"}], "m"))
        assert state.emotion.value == "calm" and state.reply == _MALFORMED  # never raises


def test_missing_relation_block_degrades_to_neutral_read():
    # non-Anthropic providers don't emit `relation`; closeness must still read a neutral signal.
    for client in _backends_valid():
        raw = client.reply_structured("sys", [{"role": "user", "content": "hi"}], "m")
        rel = validate_relation(raw.get("relation"))
        assert rel.warmth == 0.0 and rel.harm == 0.0  # all-zero neutral, no crash


# --- stats parity ----------------------------------------------------------------------------------
def test_every_backend_sets_response_stats_with_model_and_latency():
    for client, model in ((_openai(_VALID), "gpt-x"), (_minimax(_VALID), "MiniMax-Text-01")):
        client.reply_structured("sys", [{"role": "user", "content": "hi"}], model)
        assert client.last_stats is not None and client.last_stats.model == model
        assert client.last_stats.latency_ms >= 0 and client.last_thinking is None


# --- Anthropic-only features degrade (ignored, not errors) -----------------------------------------
def test_anthropic_only_features_ignored_on_other_providers(caplog):
    # thinking/effort set, but provider=minimax → builds fine (MiniMax has no such knobs), no error.
    with caplog.at_level(logging.DEBUG, logger="lumi.llm"):
        client = build_llm(Config(provider="minimax", minimax_api_key="k", thinking=True, effort="high"))
    assert isinstance(client, MiniMaxClient)
    assert "Anthropic-only" in caplog.text  # the degradation is logged once at debug


def test_anthropic_provider_keeps_thinking_and_effort():
    client = build_llm(Config(provider="anthropic", api_key="sk-test", thinking=True, effort="high"))
    assert client._thinking is True and client._effort == "high"

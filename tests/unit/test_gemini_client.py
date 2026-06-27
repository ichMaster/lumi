"""v0.39 LUMI-152 — the GeminiClient base (chat + structured emotion + safety). No paid calls.

A stubbed ``_transport`` captures the request body and returns canned ``generateContent`` responses, so
the wire shape (contents/parts, systemInstruction, responseSchema, safetySettings) and the graceful
block-degrade are exercised without the network.
"""
from __future__ import annotations

import pytest

from core.config import DEFAULT_MODEL_ALIASES, Config
from core.emotion import validate
from core.images import image_block
from core.llm import GeminiClient, LLMClient, LLMError, build_llm

_VALID = '{"reply":"привіт","emotion":"playful","intensity":0.7}'


def _resp(text: str, usage: dict | None = None, finish: str = "STOP") -> dict:
    r = {"candidates": [{"finishReason": finish, "content": {"parts": [{"text": text}]}}]}
    if usage is not None:
        r["usageMetadata"] = usage
    return r


class _Transport:
    def __init__(self, resp: dict) -> None:
        self.resp = resp
        self.bodies: list[dict] = []

    def __call__(self, url, headers, body):
        self.bodies.append(body)
        return self.resp


def _client(resp: dict) -> tuple[GeminiClient, _Transport]:
    t = _Transport(resp)
    return GeminiClient("k", _transport=t), t


# --- protocol + structured output ------------------------------------------------------------------
def test_satisfies_llmclient_protocol():
    assert isinstance(_client(_resp("{}"))[0], LLMClient)


def test_reply_structured_valid_json_through_v03_gate():
    c, _ = _client(_resp(_VALID))
    state = validate(c.reply_structured("sys", [{"role": "user", "content": "hi"}], "gemini-3.1-pro-preview"))
    assert state.reply == "привіт" and state.emotion.value == "playful" and state.intensity == 0.7


def test_structured_request_shape():
    c, t = _client(_resp(_VALID))
    c.reply_structured("SYS", [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}],
                       "gemini-3.1-pro-preview")
    body = t.bodies[-1]
    # system prompt rides systemInstruction (with the JSON shape appended), not a message
    assert body["systemInstruction"]["parts"][0]["text"].startswith("SYS")
    assert "JSON object" in body["systemInstruction"]["parts"][0]["text"]
    # contents translate assistant→model
    assert [c["role"] for c in body["contents"]] == ["user", "model"]
    # structured → JSON mode + schema
    gc = body["generationConfig"]
    assert gc["responseMimeType"] == "application/json"
    assert gc["responseSchema"]["required"] == ["reply", "emotion", "intensity"]
    # safety set to the most permissive thresholds
    assert all(s["threshold"] == "BLOCK_NONE" for s in body["safetySettings"])
    cats = {s["category"] for s in body["safetySettings"]}
    assert "HARM_CATEGORY_SEXUALLY_EXPLICIT" in cats


def test_plain_reply_no_schema():
    c, t = _client(_resp("just text"))
    assert c.reply("sys", [{"role": "user", "content": "hi"}], "m") == "just text"
    assert "responseSchema" not in t.bodies[-1]["generationConfig"]


def test_image_block_becomes_inline_data():
    img = image_block(b"\x89PNG", "image/png")
    c, t = _client(_resp(_VALID))
    c.reply_structured("sys", [{"role": "user", "content": [{"text": "глянь"}, img]}], "m")
    parts = t.bodies[-1]["contents"][0]["parts"]
    assert parts[0] == {"text": "глянь"}
    assert parts[1]["inlineData"]["mimeType"] == "image/png" and parts[1]["inlineData"]["data"]


# --- safety / graceful degrade ---------------------------------------------------------------------
def test_blocked_candidate_degrades_to_calm_never_raises():
    # finishReason SAFETY, no text → a graceful calm placeholder (a non-empty reply the v0.3 gate accepts).
    blocked = {"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}]}
    c, _ = _client(blocked)
    state = validate(c.reply_structured("sys", [{"role": "user", "content": "x"}], "m"))
    assert state.emotion.value == "calm" and state.reply  # non-empty, never raised


def test_no_candidates_degrades_to_calm():
    c, _ = _client({"promptFeedback": {"blockReason": "SAFETY"}})
    state = validate(c.reply_structured("sys", [{"role": "user", "content": "x"}], "m"))
    assert state.emotion.value == "calm" and state.reply


def test_malformed_json_degrades_to_calm():
    c, _ = _client(_resp("totally not json"))
    state = validate(c.reply_structured("sys", [{"role": "user", "content": "x"}], "m"))
    assert state.emotion.value == "calm" and state.reply == "totally not json"


def test_api_error_wrapped_as_llmerror():
    def boom(url, headers, body):
        raise RuntimeError("network down")

    c = GeminiClient("k", _transport=boom, retries=0)
    with pytest.raises(LLMError, match="Gemini call failed"):
        c.reply("sys", [{"role": "user", "content": "hi"}], "m")


# --- stats -----------------------------------------------------------------------------------------
def test_last_stats_from_usage_metadata():
    c, _ = _client(_resp(_VALID, usage={"promptTokenCount": 120, "candidatesTokenCount": 40,
                                        "cachedContentTokenCount": 10}))
    c.reply_structured("sys", [{"role": "user", "content": "hi"}], "gemini-3.1-pro-preview")
    s = c.last_stats
    assert s.input_tokens == 120 and s.output_tokens == 40 and s.cache_read_tokens == 10
    assert s.model == "gemini-3.1-pro-preview" and c.last_thinking is None


# --- factory + alias -------------------------------------------------------------------------------
def test_build_llm_builds_gemini_and_checks_key():
    assert isinstance(build_llm(Config(provider="gemini", gemini_api_key="k")), GeminiClient)
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        build_llm(Config(provider="gemini"))


def test_default_gemini_alias_present():
    assert DEFAULT_MODEL_ALIASES["gemini"] == ("gemini", "gemini-3.1-pro-preview")

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


# --- v1.3 LUMI-186: explicit cachedContents lifecycle (no paid calls) ----------------------------


class _CacheTransport:
    """Records cachedContents create/delete/patch calls; POST returns a fresh cache name."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []  # (method, url)
        self.n = 0
        self.fail_create = False

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url))
        if method == "POST":
            if self.fail_create:
                raise RuntimeError("cachedContents.create 400")
            self.n += 1
            return {"name": f"cachedContents/c{self.n}", "usageMetadata": {"totalTokenCount": 12000}}
        return {}  # DELETE / PATCH

    @property
    def creates(self) -> int:
        return sum(1 for m, _ in self.calls if m == "POST")

    @property
    def deletes(self) -> int:
        return sum(1 for m, _ in self.calls if m == "DELETE")


def _cache_client(ttl="1h"):
    gen = _Transport(_resp(_VALID))
    cache = _CacheTransport()
    c = GeminiClient("k", explicit_cache=True, cache_ttl=ttl, _transport=gen, _cache_transport=cache)
    return c, gen, cache


PREFIX = "СТАБІЛЬНИЙ ПРЕФІКС канону + памʼяті."


def test_first_flagged_call_creates_cache_and_references_it_without_system_instruction():
    c, gen, cache = _cache_client(ttl="1h")
    full = PREFIX + "\n# волатильний хвіст цього ходу"
    c.reply_structured(full, [{"role": "user", "content": "привіт"}], "gemini-3.1-pro-preview",
                       cache_prefix=PREFIX)
    # a cache was created with the 1h TTL and the prefix as its system
    assert cache.creates == 1
    # the generate body references the cache, carries NO systemInstruction, and leads with the tail
    body = gen.bodies[-1]
    assert body["cachedContent"] == "cachedContents/c1"
    assert "systemInstruction" not in body
    assert body["contents"][0]["parts"][0]["text"].startswith("\n# волатильний хвіст")
    assert c.last_cache_event == "created"


def test_create_body_carries_ttl_and_prefix():
    c, gen, cache = _cache_client(ttl="1h")
    # inspect the create call body by using a capturing cache transport
    seen = {}

    def cap(method, url, headers, body):
        if method == "POST":
            seen["body"] = body
            return {"name": "cachedContents/x", "usageMetadata": {"totalTokenCount": 9}}
        return {}

    c._cache_transport = cap
    c.reply_structured(PREFIX + "tail", [{"role": "user", "content": "hi"}], "gemini-3.1-pro-preview",
                       cache_prefix=PREFIX)
    assert seen["body"]["ttl"] == "3600s"  # 1h
    assert seen["body"]["model"] == "models/gemini-3.1-pro-preview"
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == PREFIX


def test_same_prefix_reuses_the_handle_no_second_create():
    c, gen, cache = _cache_client()
    for _ in range(3):
        c.reply_structured(PREFIX + "tail", [{"role": "user", "content": "hi"}], "m", cache_prefix=PREFIX)
    assert cache.creates == 1  # created once, reused thereafter


def test_prefix_change_recreates_once_and_deletes_the_stale():
    c, gen, cache = _cache_client()
    c.reply_structured(PREFIX + "a", [{"role": "user", "content": "hi"}], "m", cache_prefix=PREFIX)
    c.reply_structured("NEW PREFIX b", [{"role": "user", "content": "hi"}], "m", cache_prefix="NEW PREFIX")
    assert cache.creates == 2 and cache.deletes == 1  # one recreate + best-effort delete of the old
    assert c.last_cache_event == "recreated:prefix"


def test_model_switch_recreates():
    c, gen, cache = _cache_client()
    c.reply_structured(PREFIX + "t", [{"role": "user", "content": "hi"}], "gemini-3.1-pro-preview",
                       cache_prefix=PREFIX)
    c.reply_structured(PREFIX + "t", [{"role": "user", "content": "hi"}], "gemini-2.5-flash",
                       cache_prefix=PREFIX)
    assert cache.creates == 2 and c.last_cache_event == "recreated:model"


def test_cache_api_failure_degrades_to_implicit_and_the_turn_succeeds():
    c, gen, cache = _cache_client()
    cache.fail_create = True
    state = c.reply_structured(PREFIX + "tail", [{"role": "user", "content": "hi"}], "m", cache_prefix=PREFIX)
    body = gen.bodies[-1]
    assert "cachedContent" not in body  # fell back to implicit…
    assert "systemInstruction" in body   # …the full system rides as usual
    assert state["reply"] == "привіт"    # the turn still produced a reply
    assert c.last_cache_event == "fallback:create-error"


def test_off_is_byte_identical_no_cache_api_call():
    gen = _Transport(_resp(_VALID))
    cache = _CacheTransport()
    c = GeminiClient("k", explicit_cache=False, _transport=gen, _cache_transport=cache)
    c.reply_structured(PREFIX + "tail", [{"role": "user", "content": "hi"}], "m", cache_prefix=PREFIX)
    assert cache.calls == []  # the caches API is never touched
    body = gen.bodies[-1]
    assert "cachedContent" not in body and "systemInstruction" in body  # today's payload shape


def test_tool_loop_rounds_never_reference_the_cache():
    # LUMI-184 probe finding: a cached-content request can't carry tools/tool_config (HTTP 400) —
    # and the tool-loop's rounds all carry `tools`, so the whole tool turn stays on the IMPLICIT
    # path (avoiding the 400). Explicit caching applies only to tool-less replies (below).
    c, gen, cache = _cache_client()

    def tool_exec(name, args):
        return "tool result"

    class _LoopTransport:
        def __init__(self):
            self.bodies = []
            self.step = 0

        def __call__(self, url, headers, body):
            self.bodies.append(body)
            self.step += 1
            if self.step == 1:  # round 1 → one tool call, then round 2 answers
                return {"candidates": [{"content": {"parts": [
                    {"functionCall": {"name": "find_in_file", "args": {}}}]}}]}
            return _resp(_VALID)

    lt = _LoopTransport()
    c._transport = lt
    c.reply_structured(PREFIX + "tail", [{"role": "user", "content": "hi"}], "m",
                       cache_prefix=PREFIX,
                       tools=[{"name": "find_in_file", "description": "", "input_schema": {}}],
                       tool_executor=tool_exec, max_steps=3)
    # every round carried tools → NO round set cachedContent (no 400), and no cache was created
    assert all("cachedContent" not in b for b in lt.bodies)
    assert cache.creates == 0


def test_tool_less_reply_still_references_the_cache():
    # The single-call (no-tools) path is where explicit caching applies.
    c, gen, cache = _cache_client()
    c.reply_structured(PREFIX + "tail", [{"role": "user", "content": "hi"}], "m", cache_prefix=PREFIX)
    assert cache.creates == 1 and "cachedContent" in gen.bodies[-1]

"""v0.39 LUMI-154 — Gemini thinking → the think-box (thinkingConfig / includeThoughts → last_thinking)."""
from __future__ import annotations

from core.config import Config
from core.llm import GeminiClient, build_llm

_TOOLS = [{"name": "read_file", "input_schema": {"type": "object"}}]
_STATE_JSON = '{"reply":"ок","emotion":"joy","intensity":0.9}'


def _thought(text: str) -> dict:
    return {"text": text, "thought": True}


def _resp(parts: list, usage: dict | None = None) -> dict:
    r = {"candidates": [{"finishReason": "STOP", "content": {"parts": parts}}]}
    if usage is not None:
        r["usageMetadata"] = usage
    return r


class _Queue:
    def __init__(self, responses):
        self._q = list(responses)
        self.bodies: list[dict] = []

    def __call__(self, url, headers, body):
        self.bodies.append(body)
        return self._q.pop(0)


def _client(transport, **kw) -> tuple[GeminiClient, _Queue]:
    return GeminiClient("k", _transport=transport, **kw), transport


# --- single call -----------------------------------------------------------------------------------
def test_thinking_on_requests_include_thoughts_and_fills_box():
    c, t = _client(_Queue([_resp([_thought("зважую теплоту"), {"text": _STATE_JSON}])]), thinking=True)
    c.reply_structured("sys", [{"role": "user", "content": "hi"}], "gemini-3.1-pro-preview")
    assert t.bodies[0]["generationConfig"]["thinkingConfig"]["includeThoughts"] is True
    assert c.last_thinking == "зважую теплоту"  # the thought parts feed the box
    assert c._thinking is True  # the status bar reads this


def test_thought_excluded_from_the_answer_text():
    c, _ = _client(_Queue([_resp([_thought("міркую"), {"text": "видима відповідь"}])]), thinking=True)
    assert c.reply("sys", [{"role": "user", "content": "hi"}], "m") == "видима відповідь"


def test_thinking_off_sends_no_thinking_config():
    c, t = _client(_Queue([_resp([{"text": _STATE_JSON}])]), thinking=False)
    c.reply_structured("sys", [{"role": "user", "content": "hi"}], "m")
    assert "thinkingConfig" not in t.bodies[0]["generationConfig"]
    assert c._thinking is False


def test_effort_maps_to_thinking_budget():
    for effort, budget in (("low", 1024), ("high", 8192), ("max", -1)):
        c, t = _client(_Queue([_resp([{"text": _STATE_JSON}])]), thinking=True, effort=effort)
        c.reply_structured("sys", [{"role": "user", "content": "hi"}], "m")
        assert t.bodies[0]["generationConfig"]["thinkingConfig"]["thinkingBudget"] == budget


def test_effort_unset_omits_budget_but_keeps_include_thoughts():
    c, t = _client(_Queue([_resp([{"text": _STATE_JSON}])]), thinking=True)  # no effort
    c.reply_structured("sys", [{"role": "user", "content": "hi"}], "m")
    tc = t.bodies[0]["generationConfig"]["thinkingConfig"]
    assert tc["includeThoughts"] is True and "thinkingBudget" not in tc


# --- across the tool-loop --------------------------------------------------------------------------
def test_thinking_accumulates_across_loop_rounds():
    c, t = _client(_Queue([
        _resp([_thought("крок 1"), {"functionCall": {"name": "read_file", "args": {}}}]),
        _resp([_thought("крок 2"), {"text": _STATE_JSON}]),
    ]), thinking=True)
    c.reply_structured("s", [{"role": "user", "content": "hi"}], "m",
                       tools=_TOOLS, tool_executor=lambda n, i: "x")
    assert c.last_thinking == "крок 1\nкрок 2"  # joined across rounds
    assert all("thinkingConfig" in b["generationConfig"] for b in t.bodies)  # carried per round


# --- build_llm wiring ------------------------------------------------------------------------------
def test_build_llm_threads_thinking_into_gemini():
    c = build_llm(Config(provider="gemini", gemini_api_key="k", thinking=True, effort="high"))
    assert isinstance(c, GeminiClient) and c._thinking is True and c._effort == "high"

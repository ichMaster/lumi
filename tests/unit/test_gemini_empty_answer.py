"""An empty Gemini answer is asked once more, not lost (found in the v2.4 live DoD, 2026-10-11).

`./lumi-cli think` came back with nothing: the thought call to gemini-2.5-flash returned 0 answer tokens.
The prod cache log showed it was no one-off — 59 of 279 thoughts (21%) had silently produced nothing. The
single-call paths (a tool-less thought, a tool-less structured reply) had no retry, and the blocking tool
loop retried only its structured terminal. Now each re-asks ONCE — thinking off where the model allows it
— counts both calls, and logs why the first came back empty. Fake transports; no network, no paid API.
"""

import logging

from core.agent import Core
from core.llm import _GEMINI_BLOCKED_STATE, GeminiClient
from state.local_store import JsonRepository

_STATE = '{"reply":"Ось і я","emotion":"tender","intensity":0.6}'
_TOOLS = [{"name": "web_lookup", "description": "look up", "input_schema": {"type": "object"}}]


def _resp(text: str | None, *, thoughts: int = 0, finish: str = "STOP", prompt: int = 100, out: int = 0) -> dict:
    parts = [{"text": "мовчки зважую…", "thought": True}]
    if text is not None:
        parts.append({"text": text})
    return {
        "candidates": [{"finishReason": finish, "content": {"parts": parts}}],
        "usageMetadata": {"promptTokenCount": prompt, "candidatesTokenCount": out, "thoughtsTokenCount": thoughts},
    }


EMPTY = _resp(None, thoughts=900)


class _Rounds:
    def __init__(self, responses: list[dict]):
        self.responses, self.bodies = list(responses), []

    def __call__(self, url, headers, body):
        self.bodies.append(body)
        return self.responses.pop(0)


def test_an_empty_thought_is_asked_once_more_without_thinking(caplog):
    t = _Rounds([EMPTY, _resp("море дихає повільно\nЕМОЦІЯ: calm", prompt=120, out=12)])
    gem = GeminiClient("k", _transport=t)
    with caplog.at_level(logging.WARNING, logger="lumi.llm"):
        text = gem.reply("sys", [{"role": "user", "content": "про море"}], "gemini-2.5-flash")
    assert text.startswith("море дихає повільно")
    assert len(t.bodies) == 2
    assert t.bodies[1]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}  # the answer straight out
    assert "thinkingConfig" not in t.bodies[0]["generationConfig"]  # the first call stays as configured
    assert gem.last_stats.input_tokens == 220 and gem.last_stats.output_tokens == 12  # both calls counted
    assert "finish STOP, thinking 900 tokens" in caplog.text  # why the first one was empty


def test_a_model_that_cant_disable_thinking_is_simply_asked_again():
    t = _Rounds([EMPTY, _resp("так")])
    gem = GeminiClient("k", _transport=t)
    assert gem.reply("sys", [{"role": "user", "content": "?"}], "gemini-3.1-pro-preview") == "так"
    assert t.bodies[1]["generationConfig"].get("thinkingConfig", {}).get("thinkingBudget") != 0  # a 400 otherwise


def test_two_empty_answers_end_empty_after_exactly_two_calls():
    t = _Rounds([EMPTY, EMPTY])
    gem = GeminiClient("k", _transport=t)
    assert gem.reply("sys", [{"role": "user", "content": "?"}], "gemini-2.5-flash") == ""
    assert len(t.bodies) == 2  # bounded — never a loop


def test_an_answer_on_the_first_call_is_never_asked_twice():
    t = _Rounds([_resp("одразу")])
    gem = GeminiClient("k", _transport=t)
    assert gem.reply("sys", [{"role": "user", "content": "?"}], "gemini-2.5-flash") == "одразу"
    assert len(t.bodies) == 1


def test_a_tool_less_structured_reply_is_asked_once_more_before_the_placeholder():
    t = _Rounds([EMPTY, _resp(_STATE)])
    gem = GeminiClient("k", _transport=t)
    out = gem.reply_structured("sys", [{"role": "user", "content": "?"}], "gemini-2.5-flash")
    assert out["reply"] == "Ось і я" and len(t.bodies) == 2
    t2 = _Rounds([EMPTY, EMPTY])
    assert GeminiClient("k", _transport=t2).reply_structured(
        "sys", [{"role": "user", "content": "?"}], "gemini-2.5-flash") == dict(_GEMINI_BLOCKED_STATE)
    assert len(t2.bodies) == 2


def test_the_think_path_tool_loop_re_asks_an_empty_text_round():
    """A tool-thought (`%catchup`, `%journal`…) runs the blocking loop with a TEXT terminal."""
    t = _Rounds([EMPTY, _resp("світ сьогодні гучний\nЕМОЦІЯ: thoughtful")])
    gem = GeminiClient("k", _transport=t)
    text = gem.reply("sys", [{"role": "user", "content": "?"}], "gemini-2.5-flash",
                     tools=_TOOLS, tool_executor=lambda name, args: "r")
    assert text.startswith("світ сьогодні гучний")
    assert "tools" in t.bodies[0] and "tools" not in t.bodies[1]  # the forced final round: no tools
    assert [k for k, _ in gem.last_round_log] == ["empty", "reply"]


def test_a_typed_think_lands_even_when_the_first_answer_is_empty(tmp_path):
    """The DoD failure, end to end: `%think!` through the core, a Gemini client whose first answer is empty."""
    t = _Rounds([EMPTY, _resp("хвилі рахують мої думки\nЕМОЦІЯ: tender")])
    core = Core(llm=GeminiClient("k", _transport=t), repository=JsonRepository(tmp_path / "s.json"),
                canon="Ти — Лілі.", model="gemini-2.5-flash", mood_enabled=False)
    out = core.run_directive("%think! про море", core.start_session())
    assert out.thought is not None and out.thought.text == "хвилі рахують мої думки"
    assert out.thought.emotion == "tender"

"""Two live Gemini 3.x bugs (2026-10-10, a news turn on the dev server) — regression tests.

1. The STREAMED tool loop rebuilt bare ``{"functionCall": …}`` parts for the model turn and dropped the
   ``thoughtSignature`` Gemini 3 attaches to them → the next round got HTTP 400 → the turn fell back to the
   blocking loop from scratch (the web lookup ran twice).
2. The answer round sometimes carries the reply only in its THINKING (0 visible output tokens) → the turn
   ended as the '…' placeholder while the think-box held a ready answer. It now re-asks once in the forced
   final round (schema, no tools).
"""

from core.llm import _GEMINI_BLOCKED_STATE, GeminiClient

_TOOLS = [{"name": "web_lookup", "description": "look up", "input_schema": {"type": "object"}}]
_STATE = '{"reply":"Ось що нового","emotion":"thoughtful","intensity":0.6}'


def _chunk(parts: list) -> dict:
    return {"candidates": [{"content": {"parts": parts}}]}


class _StreamRounds:
    """Each streamed round yields the next scripted chunks; every request body is recorded."""

    def __init__(self, rounds: list[list[dict]]):
        self.rounds, self.bodies = list(rounds), []

    def __call__(self, url, headers, body):
        self.bodies.append(body)
        return iter(self.rounds.pop(0))


class _BlockingRounds:
    def __init__(self, responses: list[dict]):
        self.responses, self.bodies = list(responses), []

    def __call__(self, url, headers, body):
        self.bodies.append(body)
        return self.responses.pop(0)


def _model_turn_parts(body: dict) -> list[dict]:
    return [p for c in body["contents"] if c.get("role") == "model" for p in c["parts"]]


def test_the_streamed_loop_returns_the_thought_signature_with_the_function_call():
    call_part = {"functionCall": {"name": "web_lookup", "args": {"query": "LLM news"}}, "thoughtSignature": "sig-1"}
    t = _StreamRounds([[_chunk([call_part])], [_chunk([{"text": _STATE}])]])
    gem = GeminiClient("k", _stream_transport=t)
    out = gem.reply_structured_stream("sys", [{"role": "user", "content": "новини?"}], "gemini-3.1-pro-preview",
                                      on_delta=lambda _d: None, tools=_TOOLS,
                                      tool_executor=lambda name, args: "news")
    assert out["reply"] == "Ось що нового"
    sent = _model_turn_parts(t.bodies[1])
    assert sent and sent[0].get("thoughtSignature") == "sig-1"  # the signature went back with the call


def test_a_signature_arriving_in_its_own_part_is_merged_into_the_call():
    t = _StreamRounds([
        [_chunk([{"functionCall": {"name": "web_lookup", "args": {}}}]), _chunk([{"thoughtSignature": "sig-2"}])],
        [_chunk([{"text": _STATE}])],
    ])
    gem = GeminiClient("k", _stream_transport=t)
    gem.reply_structured_stream("sys", [{"role": "user", "content": "?"}], "gemini-3.1-pro-preview",
                                on_delta=lambda _d: None, tools=_TOOLS, tool_executor=lambda n, a: "r")
    assert _model_turn_parts(t.bodies[1])[0].get("thoughtSignature") == "sig-2"


def test_a_streamed_answer_only_in_thinking_is_re_asked_once():
    t = _StreamRounds([
        [_chunk([{"text": "Вже знаю, що відповім…", "thought": True}])],  # 0 visible output
        [_chunk([{"text": _STATE}])],
    ])
    gem = GeminiClient("k", _stream_transport=t)
    shown: list[str] = []
    out = gem.reply_structured_stream("sys", [{"role": "user", "content": "?"}], "gemini-3.1-pro-preview",
                                      on_delta=shown.append, tools=_TOOLS, tool_executor=lambda n, a: "r")
    assert out["reply"] == "Ось що нового" and "".join(shown) == "Ось що нового"
    retry = t.bodies[1]
    assert "tools" not in retry and retry["generationConfig"].get("responseSchema")  # the forced final round
    assert [k for k, _ in gem.last_round_log] == ["empty", "reply"]


def test_a_blocking_answer_only_in_thinking_is_re_asked_once():
    t = _BlockingRounds([
        {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "думки…", "thought": True}]}}]},
        {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": _STATE}]}}]},
    ])
    gem = GeminiClient("k", _transport=t)
    out = gem.reply_structured("sys", [{"role": "user", "content": "?"}], "gemini-3.1-pro-preview",
                               tools=_TOOLS, tool_executor=lambda n, a: "r")
    assert out["reply"] == "Ось що нового"
    assert "tools" not in t.bodies[1] and t.bodies[1]["generationConfig"].get("responseSchema")


def test_two_empty_answers_still_end_as_the_placeholder_without_looping():
    empty = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "…", "thought": True}]}}]}
    t = _BlockingRounds([empty, empty])
    gem = GeminiClient("k", _transport=t)
    out = gem.reply_structured("sys", [{"role": "user", "content": "?"}], "gemini-3.1-pro-preview",
                               tools=_TOOLS, tool_executor=lambda n, a: "r")
    assert out == dict(_GEMINI_BLOCKED_STATE) and len(t.bodies) == 2  # one retry, then the honest placeholder

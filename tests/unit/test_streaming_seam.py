"""v1.4 LUMI-188 — reply_structured_stream across the clients (no SDK, no network)."""
from __future__ import annotations

import json
from types import SimpleNamespace

from core.llm import AnthropicClient, GeminiClient, MockLLMClient

_MSGS = [{"role": "user", "content": "hi"}]


# --- MockLLMClient ------------------------------------------------------------------------------

def test_mock_stream_assembles_to_the_blocking_reply():
    # The streamed deltas reassemble to exactly what reply_structured returns; emotion resolved at end.
    m = MockLLMClient(states={"reply": "Привіт, як ти сьогодні?", "emotion": "joy", "intensity": 0.7},
                      stream_chunk=4)
    deltas: list[str] = []
    out = m.reply_structured_stream("sys", _MSGS, "mock", on_delta=deltas.append)
    assert "".join(deltas) == "Привіт, як ти сьогодні?"
    assert len(deltas) > 1                              # actually chunked, not one shot
    assert out == {"reply": "Привіт, як ти сьогодні?", "emotion": "joy", "intensity": 0.7}


def test_mock_stream_runs_tools_then_streams_only_the_terminal_round():
    # A tool turn: the scripted tool runs (intermediate round, blocking) then the terminal reply streams.
    m = MockLLMClient(states={"reply": "готово", "emotion": "calm", "intensity": 0.4},
                      tool_script=[("read_file", {"path": "a"})])
    deltas: list[str] = []
    out = m.reply_structured_stream(
        "sys", _MSGS, "mock", on_delta=deltas.append,
        tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
        tool_executor=lambda n, i: "line 1: x",
    )
    assert m.tool_calls == [("read_file", {"path": "a"}, "line 1: x")]   # the tool DID run
    assert "".join(deltas) == "готово"                                    # only the terminal reply streamed
    assert out["reply"] == "готово"


# --- GeminiClient (SSE via an injected transport) ------------------------------------------------

def _gemini_chunks(answer_obj: dict, *, thought: str | None = None, frag: int = 6) -> list[dict]:
    js = json.dumps(answer_obj, ensure_ascii=False)
    chunks: list[dict] = []
    if thought:
        chunks.append({"candidates": [{"content": {"parts": [{"text": thought, "thought": True}]}}]})
    for i in range(0, len(js), frag):
        chunks.append({"candidates": [{"content": {"parts": [{"text": js[i:i + frag]}]}}]})
    chunks[-1]["usageMetadata"] = {"promptTokenCount": 120, "candidatesTokenCount": 20}
    return chunks


def test_gemini_stream_reassembles_partial_json_and_routes_thought():
    chunks = _gemini_chunks({"reply": "Привіт світ", "emotion": "joy", "intensity": 0.6}, thought="мірку")
    gem = GeminiClient("k", _stream_transport=lambda url, h, b: iter(chunks))
    deltas: list[str] = []
    out = gem.reply_structured_stream("sys", _MSGS, "gemini-2.5-flash", on_delta=deltas.append)
    assert "".join(deltas) == "Привіт світ"                 # decoded out of the accumulating JSON
    assert out["reply"] == "Привіт світ" and out["emotion"] == "joy"
    assert gem.last_thinking == "мірку"                     # thought parts → the think-box
    assert gem.last_stats.input_tokens == 120               # usage captured from the final chunk


def test_gemini_stream_falls_back_to_blocking_on_error():
    def _boom(url, h, b):
        raise RuntimeError("sse dropped")

    calls: list[dict] = []

    def _blocking(url, h, b):  # the blocking _transport used by reply_structured on fallback
        calls.append(b)
        answer = json.dumps({"reply": "запасний", "emotion": "calm", "intensity": 0.5})
        return {"candidates": [{"content": {"parts": [{"text": answer}]}}]}

    gem = GeminiClient("k", _transport=_blocking, _stream_transport=_boom)
    deltas: list[str] = []
    out = gem.reply_structured_stream("sys", _MSGS, "gemini-2.5-flash", on_delta=deltas.append)
    assert out["reply"] == "запасний"                       # blocking path produced the reply
    assert "".join(deltas) == "запасний"                    # emitted once via on_delta
    assert calls                                            # the blocking transport was reached


# --- AnthropicClient (input_json_delta via a fake SDK stream) ------------------------------------

def _tooluse(name, inp):
    return SimpleNamespace(type="tool_use", id=f"t_{name}", name=name, input=inp)


def _usage(i, o):
    return SimpleNamespace(input_tokens=i, output_tokens=o,
                           cache_read_input_tokens=0, cache_creation_input_tokens=0)


def _json_delta(fragment: str):
    return SimpleNamespace(type="content_block_delta",
                           delta=SimpleNamespace(type="input_json_delta", partial_json=fragment))


class _FakeStream:
    def __init__(self, events, final):
        self._events, self._final = events, final

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self._events)

    def get_final_message(self):
        return self._final


def _anthropic_stream_fake(state: dict, *, frag: int = 5):
    js = json.dumps(state, ensure_ascii=False)
    events = [_json_delta(js[i:i + frag]) for i in range(0, len(js), frag)]
    final = SimpleNamespace(content=[_tooluse("set_state", state)], usage=_usage(10, 5))

    class _M:
        def __init__(self):
            self.calls = []

        def stream(self, **kw):
            self.calls.append(("stream", kw))
            return _FakeStream(events, final)

        def create(self, **kw):  # the blocking fallback path
            self.calls.append(("create", kw))
            return final

    return SimpleNamespace(messages=_M())


def test_anthropic_stream_decodes_input_json_delta():
    state = {"reply": "Привіт, друже!", "emotion": "tender", "intensity": 0.8}
    client = AnthropicClient("sk-test", _client=_anthropic_stream_fake(state))
    deltas: list[str] = []
    out = client.reply_structured_stream("sys", _MSGS, "claude-x", on_delta=deltas.append)
    assert "".join(deltas) == "Привіт, друже!"       # streamed from the growing tool-input JSON
    assert out == state                               # authoritative state from the final message


def test_anthropic_stream_falls_back_to_blocking_on_error():
    state = {"reply": "ок", "emotion": "calm", "intensity": 0.5}
    final = SimpleNamespace(content=[_tooluse("set_state", state)], usage=_usage(1, 1))

    class _M:
        def __init__(self):
            self.calls = []

        def stream(self, **kw):
            raise RuntimeError("stream unavailable")

        def create(self, **kw):
            self.calls.append(kw)
            return final

    client = AnthropicClient("sk-test", _client=SimpleNamespace(messages=_M()))
    deltas: list[str] = []
    out = client.reply_structured_stream("sys", _MSGS, "claude-x", on_delta=deltas.append)
    assert out == state and "".join(deltas) == "ок"   # blocking fallback, emitted once

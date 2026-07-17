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


def _rounds_transport(rounds: list[list[dict]]):
    """A stateful stream transport: each _post_stream call (one per loop round) yields the next round's
    chunks. Ignores the body (the test drives the sequence)."""
    seq = list(rounds)

    def transport(url, headers, body):
        return iter(seq.pop(0))

    return transport


def test_gemini_stream_tool_round_then_streams_the_answer():
    # v1.10: a functionCall round runs the tool (nothing streamed); the answer round streams the reply.
    rounds = [
        [{"candidates": [{"content": {"parts": [{"functionCall": {"name": "recall", "args": {"query": "x"}}}]}}]}],
        _gemini_chunks({"reply": "Знайшла: Привіт", "emotion": "joy", "intensity": 0.6}),
    ]
    gem = GeminiClient("k", _stream_transport=_rounds_transport(rounds))
    ran: list = []
    deltas: list[str] = []
    out = gem.reply_structured_stream(
        "sys", _MSGS, "gemini-2.5-flash", on_delta=deltas.append,
        tools=[{"name": "recall", "input_schema": {"type": "object"}}],
        tool_executor=lambda n, a: ran.append((n, a)) or "past: hi",
    )
    assert ran == [("recall", {"query": "x"})]              # the tool ran on the (silent) tool round
    assert "".join(deltas) == "Знайшла: Привіт"             # ONLY the answer round streamed
    assert out["reply"] == "Знайшла: Привіт" and out["emotion"] == "joy"


def test_gemini_stream_with_tools_enabled_but_none_called_streams_from_round_one():
    # The common case: tools offered, but she answers directly → streams from the first round (no tool).
    rounds = [_gemini_chunks({"reply": "Привіт без інструментів", "emotion": "calm", "intensity": 0.5})]
    gem = GeminiClient("k", _stream_transport=_rounds_transport(rounds))
    deltas: list[str] = []
    out = gem.reply_structured_stream(
        "sys", _MSGS, "gemini-2.5-flash", on_delta=deltas.append,
        tools=[{"name": "recall", "input_schema": {"type": "object"}}],
        tool_executor=lambda n, a: "unused",
    )
    assert "".join(deltas) == "Привіт без інструментів"     # streamed token-by-token, tools untouched
    assert out["reply"] == "Привіт без інструментів"


def test_gemini_tool_stream_falls_back_to_blocking_loop_on_error():
    # A stream error on a tool turn degrades to the blocking loop (reply still produced, emitted once).
    def boom_stream(url, h, b):
        raise RuntimeError("sse dropped")

    answer = json.dumps({"reply": "запасний з інструментами", "emotion": "calm", "intensity": 0.5})

    def blocking(url, h, b):  # the blocking _loop's generateContent: a terminal answer, no functionCall
        return {"candidates": [{"content": {"parts": [{"text": answer}]}}]}

    gem = GeminiClient("k", _transport=blocking, _stream_transport=boom_stream)
    deltas: list[str] = []
    out = gem.reply_structured_stream(
        "sys", _MSGS, "gemini-2.5-flash", on_delta=deltas.append,
        tools=[{"name": "recall", "input_schema": {"type": "object"}}],
        tool_executor=lambda n, a: "x",
    )
    assert out["reply"] == "запасний з інструментами"
    assert "".join(deltas) == "запасний з інструментами"   # blocking loop → emitted once


def test_gemini_stream_plain_text_answer_streams_too():
    # A small model may reply as PLAIN TEXT (no {"reply":...} JSON) on the tool-round path — it must
    # still stream (not just parse at the end). Regression: strict decode showed `first == total`.
    text = "Привіт, ось моя відповідь без JSON"
    chunks = [{"candidates": [{"content": {"parts": [{"text": text[i:i + 5]}]}}]}
              for i in range(0, len(text), 5)]
    chunks[-1]["usageMetadata"] = {"promptTokenCount": 10, "candidatesTokenCount": 5}
    gem = GeminiClient("k", _stream_transport=_rounds_transport([chunks]))
    deltas: list[str] = []
    out = gem.reply_structured_stream(
        "sys", _MSGS, "gemini-2.5-flash-lite", on_delta=deltas.append,
        tools=[{"name": "recall", "input_schema": {"type": "object"}}],
        tool_executor=lambda n, a: "x",
    )
    assert "".join(deltas) == text          # streamed the plain-text answer, token-by-token
    assert out["reply"] == text             # and parsed the same reply at completion


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


def _cb_start(block_type, name=None):
    return SimpleNamespace(type="content_block_start",
                           content_block=SimpleNamespace(type=block_type, name=name))


def _cb_stop():
    return SimpleNamespace(type="content_block_stop")


def _anthropic_rounds_fake(rounds):
    """A messages.stream fake serving a list of rounds; each round is (events, final_message)."""
    seq = list(rounds)

    class _M:
        def __init__(self):
            self.calls = []

        def stream(self, **kw):
            self.calls.append(kw)
            events, final = seq.pop(0)
            return _FakeStream(events, final)

    return SimpleNamespace(messages=_M())


def test_anthropic_tool_loop_streams_the_answer_round():
    # v1.10: round 1 is a tool call (nothing streamed); the terminal set_state round streams the reply.
    state = {"reply": "Знайшла для тебе відповідь", "emotion": "joy", "intensity": 0.6}
    js = json.dumps(state, ensure_ascii=False)
    round1 = (
        [_cb_start("tool_use", "recall"), _json_delta('{"query": "x"}'), _cb_stop()],
        SimpleNamespace(content=[_tooluse("recall", {"query": "x"})], usage=_usage(20, 5)),
    )
    round2 = (
        [_cb_start("tool_use", "set_state"), *[_json_delta(js[i:i + 5]) for i in range(0, len(js), 5)], _cb_stop()],
        SimpleNamespace(content=[_tooluse("set_state", state)], usage=_usage(30, 8)),
    )
    client = AnthropicClient("sk-test", _client=_anthropic_rounds_fake([round1, round2]))
    ran: list = []
    deltas: list[str] = []
    out = client.reply_structured_stream(
        "sys", _MSGS, "claude-haiku", on_delta=deltas.append,
        tools=[{"name": "recall", "input_schema": {"type": "object"}}],
        tool_executor=lambda n, a: ran.append((n, a)) or "past: hi",
    )
    assert ran == [("recall", {"query": "x"})]                 # the tool ran on the (silent) tool round
    assert "".join(deltas) == "Знайшла для тебе відповідь"      # only the answer round streamed
    assert out == state


def test_anthropic_tool_loop_streams_from_round_one_when_no_tool_called():
    state = {"reply": "Одразу відповідаю", "emotion": "calm", "intensity": 0.5}
    js = json.dumps(state, ensure_ascii=False)
    round1 = (
        [_cb_start("tool_use", "set_state"), *[_json_delta(js[i:i + 4]) for i in range(0, len(js), 4)], _cb_stop()],
        SimpleNamespace(content=[_tooluse("set_state", state)], usage=_usage(20, 5)),
    )
    client = AnthropicClient("sk-test", _client=_anthropic_rounds_fake([round1]))
    deltas: list[str] = []
    out = client.reply_structured_stream(
        "sys", _MSGS, "claude-haiku", on_delta=deltas.append,
        tools=[{"name": "recall", "input_schema": {"type": "object"}}],
        tool_executor=lambda n, a: "unused",
    )
    assert "".join(deltas) == "Одразу відповідаю"               # streamed from the first round (no tool)
    assert out == state


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

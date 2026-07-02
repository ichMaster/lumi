"""Unit tests for the LLMClient seam (LUMI-002)."""

import pytest

from core.llm import (
    AnthropicClient,
    LLMClient,
    LLMError,
    MockLLMClient,
    _call_with_retries,
)


def test_mock_satisfies_llmclient_protocol():
    assert isinstance(MockLLMClient(), LLMClient)


def test_mock_returns_canned_reply_and_records_call_no_network():
    mock = MockLLMClient("Я тут.")
    out = mock.reply(system="canon", messages=[{"role": "user", "content": "Привіт"}], model="m1")

    assert out == "Я тут."
    assert len(mock.calls) == 1
    assert mock.calls[0]["system"] == "canon"
    assert mock.calls[0]["model"] == "m1"


def test_mock_list_consumed_in_order_then_repeats_last():
    mock = MockLLMClient(["one", "two"])
    assert mock.reply("s", [], "m") == "one"
    assert mock.reply("s", [], "m") == "two"
    assert mock.reply("s", [], "m") == "two"  # last repeats


def test_mock_callable_sees_model_id():
    # config-driven model id: whatever the core passes flows through to the backend.
    mock = MockLLMClient(lambda system, messages, model: f"model={model}")
    assert mock.reply("s", [], "claude-haiku-4-5-20251001") == "model=claude-haiku-4-5-20251001"


def test_anthropic_client_requires_api_key():
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        AnthropicClient(None)
    with pytest.raises(LLMError):
        AnthropicClient("")


def test_anthropic_client_satisfies_protocol_without_network():
    # A fake underlying client; no real Anthropic object, no network.
    class _FakeMessages:
        def create(self, **kwargs):
            class _Block:
                type = "text"
                text = "ok"

            class _Resp:
                content = [_Block()]

            return _Resp()

    class _FakeClient:
        messages = _FakeMessages()

    client = AnthropicClient("sk-test", _client=_FakeClient())
    assert isinstance(client, LLMClient)
    assert client.reply("sys", [{"role": "user", "content": "hi"}], "claude-haiku-4-5-20251001") == "ok"


class _RecordingClient:
    """Captures the kwargs passed to messages.create; returns a canned reply."""

    def __init__(self):
        self.last_kwargs = None

        class _Block:
            type = "text"
            text = "ok"

        class _Resp:
            content = [_Block()]

        class _Messages:
            def create(_self, **kwargs):
                self.last_kwargs = kwargs
                return _Resp()

        self.messages = _Messages()


def test_thinking_off_by_default_sends_no_thinking_param():
    rec = _RecordingClient()
    client = AnthropicClient("sk-test", max_tokens=1024, _client=rec)
    client.reply("sys", [{"role": "user", "content": "hi"}], "claude-opus-4-8")
    assert "thinking" not in rec.last_kwargs
    assert "output_config" not in rec.last_kwargs
    assert rec.last_kwargs["max_tokens"] == 1024


def test_thinking_enabled_sends_adaptive_and_effort():
    rec = _RecordingClient()
    client = AnthropicClient("sk-test", thinking=True, effort="medium", _client=rec)
    client.reply("sys", [{"role": "user", "content": "hi"}], "claude-opus-4-8")

    # Opus 4.8 uses adaptive thinking — never {type: enabled, budget_tokens}.
    # display "summarized" makes the reasoning summary available to render.
    assert rec.last_kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert rec.last_kwargs["output_config"] == {"effort": "medium"}


def test_thinking_summary_captured_in_last_thinking():
    class _Thinking:
        type = "thinking"
        thinking = "Користувач вітається — відповім тепло."

    class _Text:
        type = "text"
        text = "Привіт!"

    class _Resp:
        content = [_Thinking(), _Text()]

    class _Messages:
        def create(self, **kwargs):
            return _Resp()

    class _Client:
        messages = _Messages()

    client = AnthropicClient("sk-test", thinking=True, _client=_Client())
    out = client.reply("sys", [{"role": "user", "content": "привіт"}], "claude-opus-4-8")
    assert out == "Привіт!"  # only the text block is the reply
    assert client.last_thinking == "Користувач вітається — відповім тепло."


def test_last_thinking_none_when_off():
    rec = _RecordingClient()
    client = AnthropicClient("sk-test", thinking=False, _client=rec)
    client.reply("sys", [{"role": "user", "content": "hi"}], "claude-opus-4-8")
    assert client.last_thinking is None


def test_effort_can_be_set_without_thinking():
    rec = _RecordingClient()
    client = AnthropicClient("sk-test", thinking=False, effort="high", _client=rec)
    client.reply("sys", [{"role": "user", "content": "hi"}], "claude-opus-4-8")
    assert "thinking" not in rec.last_kwargs
    assert rec.last_kwargs["output_config"] == {"effort": "high"}


def test_retry_succeeds_after_transient_failures():
    calls = {"n": 0}

    class Transient(Exception):
        pass

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise Transient()
        return "done"

    out = _call_with_retries(
        flaky, retries=2, backoff=0, is_retryable=lambda e: isinstance(e, Transient)
    )
    assert out == "done"
    assert calls["n"] == 3


def test_retry_is_bounded_then_raises():
    calls = {"n": 0}

    class Transient(Exception):
        pass

    def always_fail():
        calls["n"] += 1
        raise Transient()

    with pytest.raises(Transient):
        _call_with_retries(
            always_fail, retries=2, backoff=0, is_retryable=lambda e: isinstance(e, Transient)
        )
    assert calls["n"] == 3  # 1 try + 2 retries, no hang


def test_non_retryable_raises_immediately():
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise ValueError("nope")

    with pytest.raises(ValueError):
        _call_with_retries(boom, retries=5, backoff=0, is_retryable=lambda e: False)
    assert calls["n"] == 1  # not retried


# --- v0.3 structured emotion output (reply_structured) -------------------
def test_mock_reply_structured_derives_state_from_text():
    mock = MockLLMClient("привіт")
    raw = mock.reply_structured(system="s", messages=[], model="m")
    assert raw == {"reply": "привіт", "emotion": "calm", "intensity": 0.5}
    assert len(mock.calls) == 1


def test_mock_reply_structured_returns_canned_state():
    mock = MockLLMClient(states={"reply": "ага", "emotion": "joy", "intensity": 0.9})
    assert mock.reply_structured("s", [], "m") == {"reply": "ага", "emotion": "joy", "intensity": 0.9}


def test_mock_reply_structured_scripts_malformed_states():
    # A list is consumed in order — handy for driving the validation gate.
    bad = {"reply": "x", "emotion": "ecstatic", "intensity": 5}  # unknown emotion, out of range
    mock = MockLLMClient(states=[bad, {"reply": "y", "emotion": "sad", "intensity": 0.2}])
    assert mock.reply_structured("s", [], "m")["emotion"] == "ecstatic"
    assert mock.reply_structured("s", [], "m")["emotion"] == "sad"


def _tool_client(tool_input):
    class _Tool:
        type = "tool_use"
        name = "set_state"
        input = tool_input

    class _Resp:
        content = [_Tool()]

    class _Messages:
        last_kwargs = None

        def create(self, **kwargs):
            self.last_kwargs = kwargs
            return _Resp()

    class _Client:
        messages = _Messages()

    return _Client()


def test_anthropic_reply_structured_extracts_tool_input():
    fc = _tool_client({"reply": "Привіт!", "emotion": "joy", "intensity": 0.8})
    client = AnthropicClient("sk-test", _client=fc)
    raw = client.reply_structured("sys", [{"role": "user", "content": "hi"}], "claude-haiku-4-5")
    assert raw == {"reply": "Привіт!", "emotion": "joy", "intensity": 0.8}
    # thinking off → forced tool_choice on the set_state tool.
    assert fc.messages.last_kwargs["tool_choice"] == {"type": "tool", "name": "set_state"}
    assert fc.messages.last_kwargs["tools"][0]["name"] == "set_state"


def test_anthropic_reply_structured_uses_auto_tool_choice_with_thinking():
    fc = _tool_client({"reply": "ок", "emotion": "calm", "intensity": 0.4})
    client = AnthropicClient("sk-test", thinking=True, _client=fc)
    client.reply_structured("sys", [], "claude-opus-4-8")
    # forced tool_choice is incompatible with extended thinking → auto.
    assert fc.messages.last_kwargs["tool_choice"] == {"type": "auto"}


def test_anthropic_reply_structured_degrades_to_text_without_a_tool_call():
    class _Text:
        type = "text"
        text = "просто текст"

    class _Resp:
        content = [_Text()]

    class _Messages:
        def create(self, **kwargs):
            return _Resp()

    class _Client:
        messages = _Messages()

    client = AnthropicClient("sk-test", _client=_Client())
    raw = client.reply_structured("sys", [], "claude-haiku-4-5")
    assert raw == {"reply": "просто текст"}  # the gate fills emotion=calm


# --- v0.15 prompt caching: the cache_prefix breakpoint (LUMI-067) ----------
def test_cache_prefix_splits_system_into_two_blocks():
    rec = _RecordingClient()
    client = AnthropicClient("sk-test", _client=rec)
    system = "PREFIX-STABLE\n\nTAIL-VOLATILE"
    client.reply(system, [{"role": "user", "content": "hi"}], "claude-opus-4-8",
                 cache_prefix="PREFIX-STABLE")
    sys_field = rec.last_kwargs["system"]
    assert sys_field == [
        {"type": "text", "text": "PREFIX-STABLE", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "\n\nTAIL-VOLATILE"},
    ]
    # the blocks concatenate back to the original system (byte-identical)
    assert sys_field[0]["text"] + sys_field[1]["text"] == system


def test_no_cache_prefix_keeps_plain_string_system():
    rec = _RecordingClient()
    client = AnthropicClient("sk-test", _client=rec)
    client.reply("PLAIN-SYSTEM", [{"role": "user", "content": "hi"}], "claude-opus-4-8")
    assert rec.last_kwargs["system"] == "PLAIN-SYSTEM"  # plain string, no cache_control


def test_cache_prefix_equal_to_system_is_one_block():
    # tail empty → cache_prefix == system → one cached block, no empty remainder
    rec = _RecordingClient()
    client = AnthropicClient("sk-test", _client=rec)
    client.reply("WHOLE", [{"role": "user", "content": "hi"}], "claude-opus-4-8", cache_prefix="WHOLE")
    assert rec.last_kwargs["system"] == [
        {"type": "text", "text": "WHOLE", "cache_control": {"type": "ephemeral"}}
    ]


def test_cache_prefix_not_a_prefix_falls_back_to_plain_string():
    rec = _RecordingClient()
    client = AnthropicClient("sk-test", _client=rec)
    client.reply("SYSTEM-X", [{"role": "user", "content": "hi"}], "claude-opus-4-8",
                 cache_prefix="MISMATCH")
    assert rec.last_kwargs["system"] == "SYSTEM-X"  # defensive: not a prefix → plain string


def test_cache_prefix_works_for_structured_calls_too():
    rec = _RecordingClient()
    client = AnthropicClient("sk-test", _client=rec)
    client.reply_structured("P\n\nT", [], "claude-opus-4-8", cache_prefix="P")
    sys_field = rec.last_kwargs["system"]
    assert sys_field[0]["cache_control"] == {"type": "ephemeral"} and sys_field[0]["text"] == "P"


def test_mock_client_ignores_cache_prefix():
    mock = MockLLMClient(replies="ok")
    assert mock.reply("sys", [], "m", cache_prefix="sy") == "ok"  # accepted + ignored
    st = MockLLMClient(states={"reply": "r", "emotion": "calm", "intensity": 0.5})
    assert st.reply_structured("sys", [], "m", cache_prefix="sy")["reply"] == "r"


def test_response_stats_capture_cache_read_and_write():
    # v0.15 (LUMI-068): the cache read/write token counts ride in ResponseStats.
    class _Usage:
        input_tokens = 100
        output_tokens = 20
        cache_read_input_tokens = 9800
        cache_creation_input_tokens = 2400

    class _Block:
        type = "text"
        text = "ok"

    class _Resp:
        content = [_Block()]
        usage = _Usage()

    class _Messages:
        def create(self, **kwargs):
            return _Resp()

    class _Client:
        messages = _Messages()

    client = AnthropicClient("sk-test", _client=_Client())
    client.reply("sys", [], "claude-opus-4-8")
    st = client.last_stats
    assert st.cache_read_tokens == 9800 and st.cache_write_tokens == 2400
    assert st.input_tokens == 100 and st.output_tokens == 20


def test_cache_ttl_1h_sets_extended_ttl_and_beta_header():
    # v0.17.x: the 1-hour cache marks ttl="1h" and sends the extended-cache-ttl beta header.
    rec = _RecordingClient()
    client = AnthropicClient("sk-test", cache_ttl="1h", _client=rec)
    client.reply("PREFIX·tail", [{"role": "user", "content": "hi"}], "m", cache_prefix="PREFIX")
    blocks = rec.last_kwargs["system"]
    assert isinstance(blocks, list)
    assert blocks[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert rec.last_kwargs["extra_headers"] == {"anthropic-beta": "extended-cache-ttl-2025-04-11"}


def test_cache_ttl_5m_default_no_ttl_no_beta():
    rec = _RecordingClient()
    client = AnthropicClient("sk-test", _client=rec)  # default 5m
    client.reply("PREFIX·tail", [{"role": "user", "content": "hi"}], "m", cache_prefix="PREFIX")
    blocks = rec.last_kwargs["system"]
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}  # no ttl key
    assert "extra_headers" not in rec.last_kwargs


def test_haiku_gets_no_thinking_or_effort_kwargs():
    # `/model haiku` with LUMI_THINKING/LUMI_EFFORT on must not 400: Haiku supports neither —
    # the client gates both per model (Opus/Sonnet keep them).
    rec = _RecordingClient()
    client = AnthropicClient("sk-test", thinking=True, effort="high", _client=rec)
    client.reply("sys", [{"role": "user", "content": "hi"}], "claude-haiku-4-5-20251001")
    assert "thinking" not in rec.last_kwargs and "output_config" not in rec.last_kwargs
    client.reply("sys", [{"role": "user", "content": "hi"}], "claude-opus-4-8")
    assert "thinking" in rec.last_kwargs and "output_config" in rec.last_kwargs

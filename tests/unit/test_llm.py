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

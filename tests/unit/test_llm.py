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

"""v0.18 — the build_llm provider/model factory (selection + errors, no paid calls)."""
from __future__ import annotations

import pytest

from core.config import Config
from core.llm import AnthropicClient, LLMError, build_llm


def test_anthropic_provider_builds_anthropic_client():
    cfg = Config(provider="anthropic", model="claude-opus-4-8", api_key="sk-test")
    llm = build_llm(cfg)
    assert isinstance(llm, AnthropicClient)


def test_anthropic_tier_passes_model_through_unchanged():
    # The tier is just the model id; the factory builds the same client for any Claude model.
    for model in ("claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-8"):
        assert isinstance(build_llm(Config(provider="anthropic", model=model, api_key="sk-test")),
                          AnthropicClient)


def test_missing_anthropic_key_raises_actionable_error():
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        build_llm(Config(provider="anthropic", api_key=None))


def test_unknown_provider_raises_with_known_set():
    with pytest.raises(LLMError, match="Unknown LLM provider"):
        build_llm(Config(provider="wat", api_key="x"))


def test_unwired_minimax_provider_raises_clear_message():
    # openai/deepseek/local are wired in LUMI-076 (their key/base_url checks live in test_llm_openai);
    # MiniMax lands in LUMI-077 → a clear "not wired yet" error until then.
    with pytest.raises(LLMError, match="not wired yet"):
        build_llm(Config(provider="minimax", api_key="x"))


def test_provider_is_case_and_space_insensitive():
    assert isinstance(build_llm(Config(provider="  Anthropic  ", api_key="sk-test")), AnthropicClient)

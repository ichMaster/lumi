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


def test_each_provider_missing_key_raises_actionable_error():
    # openai/deepseek/minimax/local are all wired (LUMI-076/077); a missing key/base_url names the var.
    with pytest.raises(LLMError, match="OPENAI_API_KEY"):
        build_llm(Config(provider="openai"))
    with pytest.raises(LLMError, match="DEEPSEEK_API_KEY"):
        build_llm(Config(provider="deepseek"))
    with pytest.raises(LLMError, match="MINIMAX_API_KEY"):
        build_llm(Config(provider="minimax"))
    with pytest.raises(LLMError, match="LUMI_LLM_BASE_URL"):
        build_llm(Config(provider="local"))


def test_provider_is_case_and_space_insensitive():
    assert isinstance(build_llm(Config(provider="  Anthropic  ", api_key="sk-test")), AnthropicClient)

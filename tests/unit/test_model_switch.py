"""v0.37 LUMI-148 — the /model runtime engine toggle: switch_model + alias resolution + config parsing.

No paid calls — a MockLLMClient stands in for both engines and an injected factory simulates the rebuild.
"""
from __future__ import annotations

import pytest

from core.agent import Core, build_core
from core.config import DEFAULT_MODEL_ALIASES, Config, _parse_model_aliases
from core.llm import LLMError, MockLLMClient
from state.local_store import JsonRepository

_ALIASES = {"opus": ("anthropic", "claude-opus-4-8"), "gpt-5.5": ("openai", "gpt-5.5")}


def _core(tmp_path, *, factory=None, aliases=None, provider="anthropic",
          model="claude-opus-4-8", llm=None) -> Core:
    return Core(
        llm=llm or MockLLMClient(states={"reply": "ок", "emotion": "joy", "intensity": 0.9}),
        repository=JsonRepository(tmp_path / "s.json"),
        canon="Ти — Лілі.", model=model, provider=provider,
        llm_factory=factory, model_aliases=aliases if aliases is not None else _ALIASES,
        mood_enabled=False, biorhythms_enabled=False, cycle_enabled=False,
    )


# --- alias resolution ------------------------------------------------------------------------------
def test_resolve_alias_is_case_insensitive(tmp_path):
    c = _core(tmp_path)
    assert c.resolve_model_target("opus") == ("anthropic", "claude-opus-4-8")
    assert c.resolve_model_target("GPT-5.5") == ("openai", "gpt-5.5")


def test_resolve_provider_model_escape_hatch(tmp_path):
    c = _core(tmp_path)
    assert c.resolve_model_target("openai:gpt-4o") == ("openai", "gpt-4o")
    assert c.resolve_model_target(" deepseek : deepseek-chat ") == ("deepseek", "deepseek-chat")


def test_resolve_unknown_alias_raises_clear_value_error(tmp_path):
    c = _core(tmp_path)
    with pytest.raises(ValueError, match="Unknown model"):
        c.resolve_model_target("bogus")
    with pytest.raises(ValueError, match="No model given"):
        c.resolve_model_target("")


def test_model_aliases_property_is_a_copy(tmp_path):
    c = _core(tmp_path)
    aliases = c.model_aliases
    aliases["x"] = ("y", "z")
    assert "x" not in c.model_aliases  # mutating the copy doesn't leak into the core


# --- switch_model ----------------------------------------------------------------------------------
def test_switch_model_rebuilds_client_and_repoints(tmp_path):
    new = MockLLMClient(states={"reply": "gpt", "emotion": "calm", "intensity": 0.3})
    built = []
    c = _core(tmp_path, factory=lambda p, m: built.append((p, m)) or new)
    assert c.provider == "anthropic" and c.model == "claude-opus-4-8"
    c.switch_model("openai", "gpt-5.5")
    assert built == [("openai", "gpt-5.5")]
    assert c._llm is new and c.model == "gpt-5.5" and c.provider == "openai"


def test_switch_model_uses_new_engine_for_next_turn(tmp_path):
    new = MockLLMClient(states={"reply": "ок", "emotion": "playful", "intensity": 0.7})
    c = _core(tmp_path, factory=lambda p, m: new)
    c.switch_model("openai", "gpt-5.5")
    state = c.reply("привіт", c.start_session())
    assert state.emotion.value == "playful" and state.reply  # the {reply,emotion,intensity} contract holds
    assert new.calls[-1]["model"] == "gpt-5.5"               # the re-pointed model reaches the new client


def test_switch_model_without_factory_raises(tmp_path):
    c = _core(tmp_path, factory=None)
    with pytest.raises(LLMError, match="isn't configured"):
        c.switch_model("openai", "gpt-5.5")


def test_switch_model_failure_keeps_old_client(tmp_path):
    old = MockLLMClient(states={"reply": "ок", "emotion": "joy", "intensity": 0.9})

    def factory(p, m):
        raise LLMError("LUMI_PROVIDER=openai needs OPENAI_API_KEY in .env.")

    c = _core(tmp_path, factory=factory, llm=old)
    with pytest.raises(LLMError, match="OPENAI_API_KEY"):
        c.switch_model("openai", "gpt-5.5")
    assert c._llm is old and c.model == "claude-opus-4-8" and c.provider == "anthropic"  # unchanged


# --- config parsing --------------------------------------------------------------------------------
def test_parse_model_aliases_defaults_and_override():
    assert _parse_model_aliases("") == dict(DEFAULT_MODEL_ALIASES)
    out = _parse_model_aliases("custom=openai:gpt-x")
    assert out["custom"] == ("openai", "gpt-x")
    assert out["opus"] == ("anthropic", "claude-opus-4-8")  # defaults kept when merging


def test_parse_model_aliases_skips_malformed_entries():
    out = _parse_model_aliases("garbage,no-colon=foo,=anthropic:x,ok=openai:gpt")
    assert out["ok"] == ("openai", "gpt")
    assert "no-colon" not in out and "garbage" not in out


def test_config_default_aliases_present():
    assert Config().model_aliases["opus"] == ("anthropic", "claude-opus-4-8")
    assert Config().model_aliases["gpt-5.5"] == ("openai", "gpt-5.5")


# --- build_core wiring -----------------------------------------------------------------------------
def test_build_core_wires_provider_aliases_and_factory(tmp_path):
    cfg = Config(store_path=tmp_path / "s.json")  # provider=anthropic, no OPENAI key
    core = build_core(config=cfg, llm=MockLLMClient(states={"reply": "ок", "emotion": "joy", "intensity": 0.9}),
                      repository=JsonRepository(tmp_path / "s.json"))
    assert core.provider == "anthropic" and core.model_aliases == cfg.model_aliases
    # The factory closure rebuilds via build_llm from the config keys → a keyless provider surfaces LLMError.
    with pytest.raises(LLMError, match="OPENAI_API_KEY"):
        core.switch_model("openai", "gpt-5.5")


# --- v0.41 LUMI-163: bare full-id → provider inference ----------------------------------------------
def test_resolve_bare_full_id_infers_provider_by_prefix(tmp_path):
    c = _core(tmp_path)
    assert c.resolve_model_target("claude-haiku-4-5-20251001") == ("anthropic", "claude-haiku-4-5-20251001")
    assert c.resolve_model_target("gpt-5.5-mini") == ("openai", "gpt-5.5-mini")
    assert c.resolve_model_target("o3-mini") == ("openai", "o3-mini")
    assert c.resolve_model_target("gemini-2.5-flash") == ("gemini", "gemini-2.5-flash")
    assert c.resolve_model_target("deepseek-chat") == ("deepseek", "deepseek-chat")


def test_resolve_alias_and_provider_id_win_over_prefix(tmp_path):
    # An alias or explicit provider:id resolves BEFORE the prefix map (existing behaviour unchanged).
    c = _core(tmp_path, aliases={"claude-x": ("openai", "not-a-claude")})
    assert c.resolve_model_target("claude-x") == ("openai", "not-a-claude")  # alias wins
    assert c.resolve_model_target("local:claude-clone") == ("local", "claude-clone")  # explicit wins


def test_resolve_unknown_prefix_still_rejected_with_hint(tmp_path):
    c = _core(tmp_path)
    with pytest.raises(ValueError, match="full model id"):
        c.resolve_model_target("llama-3-70b")

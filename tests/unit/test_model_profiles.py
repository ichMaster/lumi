"""v0.41 — model profiles: per-provider tier sets.

LUMI-160: the authored defaults + the `LUMI_MODEL_PROFILES` parser (defaults merged, malformed
skipped). LUMI-161 adds `Core.switch_profile` tests below. No paid calls.
"""
from __future__ import annotations

import pytest

from core.config import (
    DEFAULT_MODEL_PROFILES,
    Config,
    ModelProfile,
    _parse_model_profiles,
    load_config,
)


# --- LUMI-160: the authored defaults -----------------------------------------------------------------
def test_default_profiles_present_and_homogeneous():
    profiles = Config().model_profiles
    assert set(profiles) >= {"anthropic", "openai", "gemini"}
    a = profiles["anthropic"]
    assert a.provider == "anthropic" and a.reply == "claude-opus-4-8"
    assert a.think == "claude-sonnet-4-6" and a.housekeeping == "claude-haiku-4-5-20251001"
    assert profiles["openai"].provider == "openai" and profiles["openai"].reply == "gpt-5.5"
    assert profiles["gemini"].provider == "gemini" and profiles["gemini"].housekeeping == "gemini-2.5-flash-lite"
    for p in profiles.values():  # one provider per profile — the structural homogeneity rule
        assert isinstance(p, ModelProfile) and p.provider


# --- LUMI-160: the parser -----------------------------------------------------------------------------
def test_parse_profiles_empty_yields_defaults():
    assert _parse_model_profiles("") == dict(DEFAULT_MODEL_PROFILES)


def test_parse_profiles_merges_override_onto_defaults():
    out = _parse_model_profiles("custom=anthropic:m-reply,m-think,m-mood,m-hk")
    assert out["custom"] == ModelProfile("anthropic", "m-reply", "m-think", "m-mood", "m-hk")
    assert out["anthropic"] == DEFAULT_MODEL_PROFILES["anthropic"]  # defaults kept when merging


def test_parse_profiles_overrides_a_default_by_name():
    out = _parse_model_profiles("Gemini=gemini:g-r,g-t,g-m,g-h")
    assert out["gemini"] == ModelProfile("gemini", "g-r", "g-t", "g-m", "g-h")  # name lower-cased


def test_parse_profiles_skips_malformed_entries():
    raw = "garbage;no-colon=foo;three=x:a,b,c;empty=x:a,,c,d;ok=openai:r,t,m,h"
    out = _parse_model_profiles(raw)
    assert out["ok"] == ModelProfile("openai", "r", "t", "m", "h")
    for bad in ("garbage", "no-colon", "three", "empty"):
        assert bad not in out
    assert out["anthropic"] == DEFAULT_MODEL_PROFILES["anthropic"]  # defaults intact


def test_load_config_reads_the_env_var(monkeypatch):
    monkeypatch.delenv("LUMI_MODEL_PROFILES", raising=False)
    assert load_config(load_env=False).model_profiles == dict(DEFAULT_MODEL_PROFILES)
    monkeypatch.setenv("LUMI_MODEL_PROFILES", "mine=anthropic:r,t,m,h")
    cfg = load_config(load_env=False)
    assert cfg.model_profiles["mine"] == ModelProfile("anthropic", "r", "t", "m", "h")


# --- LUMI-161: Core.switch_profile --------------------------------------------------------------------
from core.agent import Core  # noqa: E402
from core.llm import LLMError, MockLLMClient  # noqa: E402
from state.local_store import JsonRepository  # noqa: E402

_PROFILES = {
    "anthropic": ModelProfile("anthropic", "claude-opus-4-8", "a-think", "a-mood", "a-hk"),
    "gemini": ModelProfile("gemini", "g-pro", "g-flash", "g-flash", "g-lite"),
}
_STATE = {"reply": "ок", "emotion": "joy", "intensity": 0.9}


def _core(tmp_path, llm, *, factory=None, profiles=_PROFILES, **kw) -> Core:
    return Core(
        llm=llm, repository=JsonRepository(tmp_path / "s.json"), canon="Ти — Лілі.",
        model="claude-opus-4-8", provider="anthropic", llm_factory=factory,
        model_profiles=profiles, mood_enabled=False, biorhythms_enabled=False,
        cycle_enabled=False, thoughts_enabled=True, **kw,
    )


def test_switch_profile_repoints_client_reply_and_all_tiers(tmp_path):
    new = MockLLMClient(replies="думка\nЕМОЦІЯ: joy", states=_STATE)
    built = []
    core = _core(tmp_path, MockLLMClient(states=_STATE), factory=lambda p, m: built.append((p, m)) or new)
    core.switch_profile("gemini")
    assert built == [("gemini", "g-pro")]
    assert core.model == "g-pro" and core.provider == "gemini" and core.profile == "gemini"
    for kind, want in (("think", "g-flash"), ("mood", "g-flash"), ("session-close", "g-lite")):
        assert core._model_for(kind) == want  # routing works on the foreign engine under its profile
    core.think("think")
    assert new.calls[-1]["model"] == "g-flash"  # a housekeeping call carries the profile tier id


def test_switch_profile_atomic_on_factory_failure(tmp_path):
    old = MockLLMClient(states=_STATE)

    def factory(p, m):
        raise LLMError("GEMINI_API_KEY missing")

    core = _core(tmp_path, old, factory=factory, model_think="env-think")
    with pytest.raises(LLMError):
        core.switch_profile("gemini")
    assert core._llm is old and core.model == "claude-opus-4-8" and core.provider == "anthropic"
    assert core._model_for("think") == "env-think" and core.profile is None  # tiers untouched


def test_switch_profile_unknown_name_is_a_clear_error(tmp_path):
    core = _core(tmp_path, MockLLMClient(states=_STATE), factory=lambda p, m: MockLLMClient(states=_STATE))
    with pytest.raises(ValueError, match="Unknown profile"):
        core.switch_profile("bogus")
    assert core.profile is None


def test_reply_only_switch_model_clears_the_profile_mark(tmp_path):
    new = MockLLMClient(states=_STATE)
    core = _core(tmp_path, MockLLMClient(states=_STATE), factory=lambda p, m: new)
    core.switch_profile("anthropic")
    assert core.profile == "anthropic"
    core.switch_model("anthropic", "claude-sonnet-4-6")  # the /model reply-only path
    assert core.profile is None  # the stack no longer matches a named set
    assert core._model_for("think") == "a-think"  # the tiers keep their current values


def test_env_var_mode_guard_unchanged_without_a_profile(tmp_path):
    # No active profile + a foreign engine → the v0.40 guard still disables routing (byte-identical).
    new = MockLLMClient(states=_STATE)
    core = _core(tmp_path, MockLLMClient(states=_STATE), factory=lambda p, m: new,
                 model_think="claude-sonnet-4-6")
    core.switch_model("gemini", "gemini-3.1-pro")
    assert core._model_for("think") == "gemini-3.1-pro"  # guard: no Claude id on a foreign engine

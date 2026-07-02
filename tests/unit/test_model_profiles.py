"""v0.41 — model profiles: per-provider tier sets.

LUMI-160: the authored defaults + the `LUMI_MODEL_PROFILES` parser (defaults merged, malformed
skipped). LUMI-161 adds `Core.switch_profile` tests below. No paid calls.
"""
from __future__ import annotations

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

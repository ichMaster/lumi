"""v0.41 — model profiles: per-provider tier sets.

LUMI-160: the authored defaults + the `LUMI_MODEL_PROFILES` parser (defaults merged, malformed
skipped). LUMI-161 adds `Core.switch_profile` tests below. No paid calls.
"""
from __future__ import annotations

import pytest

from core.config import (
    DEFAULT_MODEL_ALIASES,
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
    assert a.think == "claude-sonnet-5" and a.housekeeping == "claude-haiku-4-5-20251001"
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
    assert {"anthropic", "openai", "gemini"} <= set(load_config(load_env=False).model_profiles)
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


# --- LUMI-162: the /model-set TUI command + status bar ------------------------------------------------
async def test_model_set_lists_profiles_and_marks_active(tmp_path):
    from tui.app import ChatInput, LumiApp

    new = MockLLMClient(states=_STATE)
    core = _core(tmp_path, MockLLMClient(states=_STATE), factory=lambda p, m: new)
    core.switch_profile("anthropic")
    app = LumiApp(core)
    async with app.run_test() as pilot:
        app.query_one("#prompt", ChatInput).text = "/model-set"
        await pilot.press("enter")
        await pilot.pause()
        joined = "\n".join(app.transcript)
        assert "Профілі моделей" in joined and "gemini" in joined
        assert "← активний" in joined  # the active profile is marked


async def test_model_set_switches_the_whole_stack_and_status_shows_profile(tmp_path):
    from tui.app import ChatInput, LumiApp

    new = MockLLMClient(states=_STATE)
    core = _core(tmp_path, MockLLMClient(states=_STATE), factory=lambda p, m: new)
    app = LumiApp(core)
    async with app.run_test() as pilot:
        app.query_one("#prompt", ChatInput).text = "/model-set gemini"
        await pilot.press("enter")
        await pilot.pause()
        assert core.profile == "gemini" and core.model == "g-pro"
        assert core._model_for("session-close") == "g-lite"
        assert any("Профіль:" in line and "gemini" in line for line in app.transcript)
        assert "gemini:" in app._status_text()  # the status bar carries the profile mark


async def test_model_set_unknown_profile_is_non_fatal(tmp_path):
    from tui.app import ChatInput, LumiApp

    core = _core(tmp_path, MockLLMClient(states=_STATE), factory=lambda p, m: MockLLMClient(states=_STATE))
    app = LumiApp(core)
    async with app.run_test() as pilot:
        app.query_one("#prompt", ChatInput).text = "/model-set bogus"
        await pilot.press("enter")
        await pilot.pause()
        assert any("Unknown profile" in line for line in app.transcript)
        assert core.profile is None and core.model == "claude-opus-4-8"  # nothing changed


async def test_model_and_model_set_do_not_collide(tmp_path):
    from tui.app import ChatInput, LumiApp

    new = MockLLMClient(states=_STATE)
    core = _core(tmp_path, MockLLMClient(states=_STATE), factory=lambda p, m: new,
                 model_aliases={"sonnet": ("anthropic", "claude-sonnet-4-6")})
    app = LumiApp(core)
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", ChatInput)
        prompt.text = "/model-set anthropic"  # must hit /model-set, not /model with arg "-set …"
        await pilot.press("enter")
        await pilot.pause()
        assert core.profile == "anthropic"
        prompt.text = "/model sonnet"  # the reply-only path must hit /model and drop the mark
        await pilot.press("enter")
        await pilot.pause()
        assert core.model == "claude-sonnet-4-6" and core.profile is None
        assert "anthropic:" not in app._status_text()  # the profile mark is gone


# --- LUMI-164: LUMI_MODEL_PROFILE — boot the stack from a named profile --------------------------------
_MODEL_VARS = ("LUMI_PROVIDER", "LUMI_MODEL", "LUMI_MODEL_THINK", "LUMI_MODEL_MOOD",
               "LUMI_MODEL_HOUSEKEEPING", "LUMI_MODEL_PROFILE", "LUMI_MODEL_PROFILES")


def _clean_env(monkeypatch):
    for var in _MODEL_VARS:
        monkeypatch.delenv(var, raising=False)


def test_startup_profile_boots_the_whole_stack(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("LUMI_MODEL_PROFILE", "anthropic")
    cfg = load_config(load_env=False)
    prof = cfg.model_profiles["anthropic"]  # file-proof: assert against the loaded profile
    assert cfg.provider == "anthropic" and cfg.model == prof.reply
    assert cfg.model_think == prof.think and cfg.model_mood == prof.mood
    assert cfg.model_housekeeping == prof.housekeeping
    assert cfg.model_profile == "anthropic"


def test_explicit_env_var_wins_over_the_profile_field(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("LUMI_MODEL_PROFILE", "anthropic")
    monkeypatch.setenv("LUMI_MODEL_THINK", "claude-haiku-4-5-20251001")  # expert override
    cfg = load_config(load_env=False)
    assert cfg.model_think == "claude-haiku-4-5-20251001"  # the explicit var wins
    assert cfg.model_mood == cfg.model_profiles["anthropic"].mood  # the rest still from the profile


def test_unknown_or_unset_profile_is_pure_env_mode(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("LUMI_MODEL_PROFILE", "bogus")
    cfg = load_config(load_env=False)
    assert cfg.model_profile == "" and cfg.provider == "anthropic"
    assert cfg.model_think == ""  # no tier routing — today's env mode, byte-identical
    _clean_env(monkeypatch)
    assert load_config(load_env=False).model_profile == ""


def test_core_boots_with_the_profile_marked(tmp_path):
    core = Core(
        llm=MockLLMClient(states=_STATE), repository=JsonRepository(tmp_path / "s.json"),
        canon="Ти — Лілі.", model="g-pro", provider="gemini", model_profiles=_PROFILES,
        active_profile="gemini", model_think="g-flash", model_mood="g-flash",
        model_housekeeping="g-lite", mood_enabled=False, biorhythms_enabled=False,
        cycle_enabled=False,
    )
    assert core.profile == "gemini"
    assert core._model_for("think") == "g-flash"  # routing on at boot (the guard sees the profile)


def test_core_ignores_an_unknown_startup_profile(tmp_path):
    core = _core(tmp_path, MockLLMClient(states=_STATE), active_profile="bogus")
    assert core.profile is None


# --- LUMI-165: core/models.toml — THE editable models file ---------------------------------------------
from core.config import DEFAULT_MODELS_PATH, _load_models_file  # noqa: E402


def test_shipped_models_toml_parses_aliases_and_the_three_families():
    # Structural, not exact-equality — the owner EDITS this file when new models release.
    aliases, profiles = _load_models_file(DEFAULT_MODELS_PATH)
    assert {"opus", "sonnet", "haiku", "gemini"} <= set(aliases)
    assert {"anthropic", "openai", "gemini"} <= set(profiles)
    for p in profiles.values():  # homogeneous, fully populated sets
        assert p.provider and p.reply and p.think and p.mood and p.housekeeping


def test_models_file_edit_changes_a_profile_and_an_alias(tmp_path):
    f = tmp_path / "models.toml"
    f.write_text(
        '[aliases]\nopus = "anthropic:claude-opus-5"\n'
        '[profiles.anthropic]\nprovider="anthropic"\nreply="claude-x"\nthink="t"\nmood="m"\nhousekeeping="h"\n',
        encoding="utf-8",
    )
    aliases, profiles = _load_models_file(f)
    assert aliases["opus"] == ("anthropic", "claude-opus-5")
    assert profiles["anthropic"] == ModelProfile("anthropic", "claude-x", "t", "m", "h")


def test_models_file_missing_or_malformed_is_skipped(tmp_path):
    assert _load_models_file(tmp_path / "nope.toml") == ({}, {})
    bad = tmp_path / "bad.toml"
    bad.write_text("not [ valid toml", encoding="utf-8")
    assert _load_models_file(bad) == ({}, {})
    partial = tmp_path / "partial.toml"
    partial.write_text(
        '[aliases]\ngood = "p:m"\nbad = "no-colon"\n'
        '[profiles.ok]\nprovider="p"\nreply="r"\nthink="t"\nmood="m"\nhousekeeping="h"\n'
        '[profiles.incomplete]\nprovider="p"\nreply="r"\n',
        encoding="utf-8",
    )
    aliases, profiles = _load_models_file(partial)
    assert "good" in aliases and "bad" not in aliases
    assert "ok" in profiles and "incomplete" not in profiles  # malformed skipped, good kept


def test_models_file_aliases_feed_config_and_env_wins(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.delenv("LUMI_MODEL_ALIASES", raising=False)
    monkeypatch.delenv("LUMI_MODELS_FILE", raising=False)
    f = tmp_path / "models.toml"
    f.write_text('[aliases]\nopus = "anthropic:claude-opus-5"\n', encoding="utf-8")
    monkeypatch.setenv("LUMI_MODELS_FILE", str(f))
    cfg = load_config(load_env=False)
    assert cfg.model_aliases["opus"] == ("anthropic", "claude-opus-5")  # the file wins over code
    assert cfg.model_aliases["haiku"] == DEFAULT_MODEL_ALIASES["haiku"]  # defaults hold
    monkeypatch.setenv("LUMI_MODEL_ALIASES", "opus=anthropic:claude-opus-6")
    assert load_config(load_env=False).model_aliases["opus"] == ("anthropic", "claude-opus-6")  # env wins


def test_merge_order_defaults_file_env(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    f = tmp_path / "models.toml"
    f.write_text(
        '[profiles.gemini]\nprovider="gemini"\nreply="file-r"\nthink="file-t"\nmood="file-m"\nhousekeeping="file-h"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("LUMI_MODELS_FILE", str(f))
    cfg = load_config(load_env=False)
    assert cfg.model_profiles["gemini"].reply == "file-r"  # the file wins over the code default
    assert cfg.model_profiles["anthropic"] == DEFAULT_MODEL_PROFILES["anthropic"]  # defaults hold
    monkeypatch.setenv("LUMI_MODEL_PROFILES", "gemini=gemini:env-r,env-t,env-m,env-h")
    cfg = load_config(load_env=False)
    assert cfg.model_profiles["gemini"].reply == "env-r"  # the env var wins over the file

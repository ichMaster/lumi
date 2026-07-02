"""v0.40 LUMI-155 — Layer 1 per-operation model routing.

`_model_for(kind)` routes the internal kinds (`think` / `mood` / `session-start` / `session-close` /
`compaction` / bare `housekeeping`) to their configured Claude tiers while the visible reply stays on
the default model. Unset tiers → byte-identical (every call on the default). Provider guard: routing
is a no-op while the active engine isn't Anthropic (a Claude tier id never reaches a foreign client).
No paid calls — a MockLLMClient records the `model` per call.
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.config import load_config
from core.llm import MockLLMClient
from state.local_store import JsonRepository

_DAY = fixed_clock(datetime(2026, 6, 9, 14, 30, tzinfo=UTC))
_DEFAULT = "claude-opus-4-8"
_TIERS = {"model_think": "tier-think", "model_mood": "tier-mood", "model_housekeeping": "tier-hk"}
_STATE = {"reply": "ок", "emotion": "joy", "intensity": 0.9}


def _core(tmp_path, llm, **kw) -> Core:
    kw.setdefault("mood_enabled", False)
    kw.setdefault("biorhythms_enabled", False)
    kw.setdefault("cycle_enabled", False)
    kw.setdefault("thoughts_enabled", False)
    return Core(
        llm=llm, repository=JsonRepository(tmp_path / "s.json"), canon="Ти — Лілі.",
        model=_DEFAULT, clock=_DAY, **kw,
    )


# --- the resolver ----------------------------------------------------------------------------------
def test_model_for_maps_kinds_to_tiers(tmp_path):
    core = _core(tmp_path, MockLLMClient(states=_STATE), **_TIERS)
    assert core._model_for("think") == "tier-think"
    assert core._model_for("mood") == "tier-mood"
    for kind in ("session-start", "session-close", "compaction", "housekeeping"):
        assert core._model_for(kind) == "tier-hk"


def test_model_for_unset_tiers_fall_back_to_default(tmp_path):
    core = _core(tmp_path, MockLLMClient(states=_STATE))  # no tier overrides
    for kind in ("think", "mood", "session-start", "session-close", "compaction"):
        assert core._model_for(kind) == _DEFAULT


# --- the routed calls (mock records the model per call) --------------------------------------------
def test_reply_stays_on_default_and_session_close_routes_housekeeping(tmp_path):
    llm = MockLLMClient(replies="підсумок", states=_STATE)
    core = _core(tmp_path, llm, **_TIERS)
    session = core.start_session()
    core.reply("привіт", session)
    assert llm.calls[-1]["model"] == _DEFAULT  # the visible reply — untouched by routing
    before_close = len(llm.calls)
    core.end_session(session)
    close_models = {c["model"] for c in llm.calls[before_close:]}
    assert close_models == {"tier-hk"}  # summary + facts (kind="session-close") on the housekeeping tier


def test_think_routes_to_think_tier(tmp_path):
    llm = MockLLMClient(replies="маленька думка\nЕМОЦІЯ: joy")
    core = _core(tmp_path, llm, thoughts_enabled=True, **_TIERS)
    thought = core.think("think")
    assert thought is not None
    assert llm.calls[-1]["model"] == "tier-think"


def test_mood_call_routes_to_mood_tier(tmp_path):
    llm = MockLLMClient(replies="читання дня", states=_STATE)
    core = _core(tmp_path, llm, mood_enabled=True, natal="Скорпіон, 1 листопада", **_TIERS)
    session = core.start_session()
    core.reply("привіт", session)
    models = [c["model"] for c in llm.calls]
    assert "tier-mood" in models  # the daily mood call routed
    assert models[-1] == _DEFAULT  # the turn itself stays on the default


def test_think_tool_loop_follows_the_routed_model(tmp_path):
    # The tool-loop runs inside the one routed call, so its rounds carry the op's model (the contract:
    # routing an operation routes its whole loop).
    llm = MockLLMClient(replies="думка\nЕМОЦІЯ: joy", tool_script=[("list_files", {})])
    core = _core(tmp_path, llm, **_TIERS)
    core._housekeeping_reply(
        "s", [{"role": "user", "content": "x"}], kind="think",
        tools=[{"name": "list_files"}], tool_executor=lambda name, inp: "ok",
    )
    assert llm.calls[-1]["model"] == "tier-think"
    assert llm.last_round_log and all(stats.model == "tier-think" for _tag, stats in llm.last_round_log)


# --- byte-identical guard --------------------------------------------------------------------------
def test_unset_tiers_every_call_uses_the_default_model(tmp_path):
    llm = MockLLMClient(replies="х\nЕМОЦІЯ: joy", states=_STATE)
    core = _core(tmp_path, llm, thoughts_enabled=True)  # no tier overrides
    session = core.start_session()
    core.reply("привіт", session)
    core.think("think")
    core.end_session(session)
    assert {c["model"] for c in llm.calls} == {_DEFAULT}


# --- provider guard --------------------------------------------------------------------------------
def test_provider_guard_no_routing_on_a_foreign_engine(tmp_path):
    foreign = MockLLMClient(replies="думка\nЕМОЦІЯ: joy", states=_STATE)
    llm = MockLLMClient(replies="думка\nЕМОЦІЯ: joy", states=_STATE)
    core = _core(
        tmp_path, llm, thoughts_enabled=True, provider="anthropic",
        llm_factory=lambda p, m: foreign, **_TIERS,
    )
    core.switch_model("gemini", "gemini-3.1-pro")
    assert core._model_for("think") == "gemini-3.1-pro"  # the guard: no Claude tier id
    core.think("think")
    assert foreign.calls[-1]["model"] == "gemini-3.1-pro"
    # Switching back to Anthropic resumes the routing.
    core.switch_model("anthropic", _DEFAULT)
    core.think("think")
    assert foreign.calls[-1]["model"] == "tier-think"


# --- config ----------------------------------------------------------------------------------------
def test_config_reads_the_tier_vars(monkeypatch):
    for var in ("LUMI_MODEL_THINK", "LUMI_MODEL_MOOD", "LUMI_MODEL_HOUSEKEEPING",
                "LUMI_MODEL_PROFILE"):  # v0.41: a leaked profile would fill the tiers
        monkeypatch.delenv(var, raising=False)
    cfg = load_config(load_env=False)
    assert cfg.model_think == "" and cfg.model_mood == "" and cfg.model_housekeeping == ""
    monkeypatch.setenv("LUMI_MODEL_THINK", "claude-sonnet-4-6")
    monkeypatch.setenv("LUMI_MODEL_MOOD", "claude-sonnet-4-6")
    monkeypatch.setenv("LUMI_MODEL_HOUSEKEEPING", " claude-haiku-4-5 ")
    cfg = load_config(load_env=False)
    assert cfg.model_think == "claude-sonnet-4-6"
    assert cfg.model_mood == "claude-sonnet-4-6"
    assert cfg.model_housekeeping == "claude-haiku-4-5"  # whitespace stripped


# --- the reply-tier dial (/model, shipped v0.37) composes with routing (LUMI-156) -------------------
def test_tier_swap_moves_reply_while_routed_ops_keep_tiers(tmp_path):
    llm = MockLLMClient(replies="думка\nЕМОЦІЯ: joy", states=_STATE)
    core = _core(
        tmp_path, llm, thoughts_enabled=True, provider="anthropic",
        llm_factory=lambda p, m: llm, **_TIERS,
    )
    core.switch_model("anthropic", "claude-haiku-4-5")  # the /model haiku dial
    session = core.start_session()
    core.reply("привіт", session)
    assert llm.calls[-1]["model"] == "claude-haiku-4-5"  # the reply follows the dial
    core.think("think")
    assert llm.calls[-1]["model"] == "tier-think"  # routed ops keep their configured tiers
    before_close = len(llm.calls)
    core.end_session(session)
    assert {c["model"] for c in llm.calls[before_close:]} == {"tier-hk"}


def test_tier_swap_with_tiers_unset_moves_everything(tmp_path):
    # The v0.37 behaviour, unchanged by routing: no tier vars → the dial moves every call.
    llm = MockLLMClient(replies="думка\nЕМОЦІЯ: joy", states=_STATE)
    core = _core(
        tmp_path, llm, thoughts_enabled=True, provider="anthropic",
        llm_factory=lambda p, m: llm,
    )
    core.switch_model("anthropic", "claude-sonnet-4-6")
    session = core.start_session()
    core.reply("привіт", session)
    core.think("think")
    core.end_session(session)
    assert {c["model"] for c in llm.calls} == {"claude-sonnet-4-6"}

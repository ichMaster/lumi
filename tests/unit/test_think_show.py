"""v0.38 LUMI-150 — LUMI_THINK_SHOW modes + the think-log tier (logged, never persisted).

No paid calls — a MockLLMClient returns a reply carrying a <think> block so the monologue is captured.
"""
from __future__ import annotations

import logging

from core.agent import Core
from core.config import load_config
from core.llm import MockLLMClient
from state.local_store import JsonRepository

# The model wraps its reasoning in <think>…</think>; split_reasoning lifts it to last_thinking.
_THINK_REPLY = {"reply": "<think>таємні міркування</think>Привіт!", "emotion": "joy", "intensity": 0.8}


def _core(tmp_path, think_show="debug") -> Core:
    return Core(
        llm=MockLLMClient(states=dict(_THINK_REPLY)),
        repository=JsonRepository(tmp_path / "s.json"),
        canon="Ти — Лілі.", model="m", think_show=think_show,
        mood_enabled=False, biorhythms_enabled=False, cycle_enabled=False,
    )


# --- config ----------------------------------------------------------------------------------------
def test_config_parses_think_show(monkeypatch):
    for value in ("debug", "open", "off"):
        monkeypatch.setenv("LUMI_THINK_SHOW", value)
        assert load_config(load_env=False).think_show == value


def test_config_invalid_or_unset_defaults_to_debug(monkeypatch):
    monkeypatch.setenv("LUMI_THINK_SHOW", "bogus")
    assert load_config(load_env=False).think_show == "debug"
    monkeypatch.delenv("LUMI_THINK_SHOW", raising=False)
    assert load_config(load_env=False).think_show == "debug"


# --- the Core property + validation ----------------------------------------------------------------
def test_think_show_property_and_validation(tmp_path):
    for mode in ("debug", "open", "off"):
        assert _core(tmp_path, mode).think_show == mode
    assert _core(tmp_path, "bogus").think_show == "debug"  # invalid → debug


# --- the log tier ----------------------------------------------------------------------------------
def test_monologue_logged_when_not_off(tmp_path, caplog):
    core = _core(tmp_path, "debug")
    with caplog.at_level(logging.INFO, logger="lumi.think"):
        core.reply("привіт", core.start_session())
    assert "таємні міркування" in caplog.text  # the logged tier


def test_monologue_not_logged_when_off(tmp_path, caplog):
    core = _core(tmp_path, "off")
    with caplog.at_level(logging.INFO, logger="lumi.think"):
        core.reply("привіт", core.start_session())
    assert "таємні міркування" not in caplog.text  # off → silent (no box, no log)


# --- never persisted to long-term memory -----------------------------------------------------------
def test_monologue_is_captured_but_never_persisted(tmp_path):
    store = tmp_path / "s.json"
    core = Core(
        llm=MockLLMClient(states=dict(_THINK_REPLY)), repository=JsonRepository(store),
        canon="Ти — Лілі.", model="m", mood_enabled=False, biorhythms_enabled=False, cycle_enabled=False,
    )
    session = core.start_session()
    state = core.reply("привіт", session)
    core.end_session(session)  # flush summaries/facts to the store
    assert core.last_thinking == "таємні міркування"   # captured in the ephemeral tier
    assert state.reply == "Привіт!"                     # the visible reply is clean (think stripped)
    assert "таємні міркування" not in store.read_text(encoding="utf-8")  # never written to storage

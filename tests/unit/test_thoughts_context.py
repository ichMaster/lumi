"""Thought prompt context mode — lean (seeds) vs full (whole backdrop) (v0.12, LUMI_THOUGHTS_CONTEXT)."""

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from state.local_store import JsonRepository

_DAY = fixed_clock(datetime(2026, 6, 10, 14, 0, tzinfo=UTC))


def _core(tmp_path, *, context, llm=None):
    return Core(llm=llm or MockLLMClient("нова думка\nЕМОЦІЯ: calm"),
                repository=JsonRepository(tmp_path / "s.json"),
                canon="Ти — Лілі, з Києва.", model="m", clock=_DAY, mood_enabled=False,
                thoughts_context=context)


def test_lean_is_the_default_and_sends_a_dedicated_prompt(tmp_path):
    llm = MockLLMClient("думка\nЕМОЦІЯ: calm")
    core = _core(tmp_path, context="lean", llm=llm)
    s = core.start_session()
    core.reply("привіт, як справи з деплоєм?", s)  # a real exchange in the history
    llm.calls.clear()
    core.think("think", session=s)
    sys = llm.calls[-1]["system"]
    assert "внутрішня думка" in sys  # the lean thought directive
    assert "Ти — Лілі, з Києва." not in sys  # the canon / full backdrop is NOT in the lean prompt
    # the lean call is a single dedicated user message (the seeds), not the conversation window
    assert len(llm.calls[-1]["messages"]) == 1


def test_full_sends_the_whole_backdrop(tmp_path):
    llm = MockLLMClient("думка про деплой\nЕМОЦІЯ: thoughtful")
    core = _core(tmp_path, context="full", llm=llm)
    s = core.start_session()
    core.reply("привіт, як справи з деплоєм?", s)
    llm.calls.clear()
    t = core.think("think", session=s)
    assert t is not None and t.text == "думка про деплой"
    call = llm.calls[-1]
    assert "Ти — Лілі, з Києва." in call["system"]      # the canon IS in the full backdrop
    assert "не відповідь, а ТВОЯ внутрішня думка" in call["system"]  # reply task → thought task
    convo = str(call["messages"])
    assert "деплоєм" in convo  # the actual conversation window is sent
    assert t.seeds == ("context",)  # recorded as a full-context thought


def test_full_records_normally(tmp_path):
    core = _core(tmp_path, context="full")
    s = core.start_session()
    t = core.think("think", topic="море", session=s)
    assert t is not None and t.kind == "think"
    assert t.seeds == ("context", "topic")  # topic still tracked

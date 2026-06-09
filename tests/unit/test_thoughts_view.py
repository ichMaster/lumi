"""/thoughts view + thoughts_show policy + logged, never-persisted (v0.12, LUMI-052)."""

import logging
from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from core.repository import make_thought
from state.local_store import JsonRepository

_NOW = fixed_clock(datetime(2026, 6, 9, 20, 0, tzinfo=UTC))


def _core(tmp_path, *, user="owner", show="hidden"):
    return Core(llm=MockLLMClient("думка\nЕМОЦІЯ: calm"), repository=JsonRepository(tmp_path / "s.json"),
                canon="C", model="m", clock=_NOW, mood_enabled=False, user_id=user, thoughts_show=show)


def _add(repo, when, text, user="owner"):
    repo.add_thought(make_thought(when, "think", text, "calm", ["mood"], user))


def test_view_is_dated_and_per_user(tmp_path):
    core = _core(tmp_path)
    _add(core._repo, "2026-06-08T09:00", "yesterday")
    _add(core._repo, "2026-06-09T18:00", "today")
    view = core.thoughts_view()
    assert "yesterday" in view and "today" in view  # 7-day view spans both
    assert "09:00" in view and "18:00" in view  # dated


def test_view_empty_is_none(tmp_path):
    assert _core(tmp_path).thoughts_view() is None


def test_view_is_isolation_filtered(tmp_path):
    p = tmp_path / "s.json"
    a = Core(llm=MockLLMClient("x"), repository=JsonRepository(p), canon="C", model="m",
             clock=_NOW, mood_enabled=False, user_id="alice")
    b = Core(llm=MockLLMClient("x"), repository=JsonRepository(p), canon="C", model="m",
             clock=_NOW, mood_enabled=False, user_id="bob")
    _add(a._repo, "2026-06-09T10:00", "alice's diary", user="alice")
    assert "alice" in (a.thoughts_view() or "")
    assert b.thoughts_view() is None  # bob never sees alice's stream


def test_thoughts_show_policy(tmp_path):
    assert _core(tmp_path).thoughts_show == "hidden"
    assert _core(tmp_path, show="off").thoughts_show == "off"


def test_thoughts_logged_but_not_in_long_term_memory(tmp_path, caplog):
    core = _core(tmp_path)
    with caplog.at_level(logging.INFO, logger="lumi.thoughts"):
        t = core.think("think")
    assert t is not None
    assert any("думка" in r.getMessage() for r in caplog.records)  # logged tier
    assert core._repo.facts("owner") == []  # never written to long-term memory (facts)

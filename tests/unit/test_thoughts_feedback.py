"""Feedback block — the last-24h dated diary in the prompt + mood nudge (v0.12, LUMI-049)."""

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from core.mood import mood_request
from core.repository import make_thought
from core.thoughts import thoughts_diary_block
from state.local_store import JsonRepository

_NOW = fixed_clock(datetime(2026, 6, 9, 20, 0, tzinfo=UTC))


def _core(tmp_path, **kw):
    return Core(llm=MockLLMClient("x"), repository=JsonRepository(tmp_path / "s.json"),
                canon="C", model="m", clock=_NOW, mood_enabled=False, **kw)


def _add(repo, when, text, user="owner"):
    repo.add_thought(make_thought(when, "think", text, "calm", ["mood"], user))


# --- the pure formatter ---------------------------------------------------
def test_diary_block_is_dated_and_capped():
    items = [make_thought(f"2026-06-09T{h:02d}:00", "think", f"t{h}", "calm", [], "o") for h in range(10, 16)]
    block = thoughts_diary_block(items, max_lines=3)
    assert block == "- 13:00 — t13\n- 14:00 — t14\n- 15:00 — t15"  # capped to the newest 3, HH:MM
    assert thoughts_diary_block([]) is None


# --- the Core prompt block (24h window, per-user, empty→none) --------------
def test_block_carries_only_last_24h(tmp_path):
    core = _core(tmp_path)
    _add(core._repo, "2026-06-08T19:00", "stale (25h ago)")
    _add(core._repo, "2026-06-09T08:00", "this morning")
    _add(core._repo, "2026-06-09T18:30", "this evening")
    block = core._thoughts_block()
    assert "this morning" in block and "this evening" in block
    assert "stale" not in block  # outside the 24h window
    assert "08:00" in block  # dated


def test_empty_window_yields_no_block(tmp_path):
    core = _core(tmp_path)
    assert core._thoughts_block() is None  # nothing → no block (never blocks a turn)
    _add(core._repo, "2026-06-01T08:00", "long ago")
    assert core._thoughts_block() is None  # all outside the window → no block


def test_block_is_isolation_filtered(tmp_path):
    p = tmp_path / "s.json"
    a = Core(llm=MockLLMClient("x"), repository=JsonRepository(p), canon="C", model="m",
             clock=_NOW, mood_enabled=False, user_id="alice")
    b = Core(llm=MockLLMClient("x"), repository=JsonRepository(p), canon="C", model="m",
             clock=_NOW, mood_enabled=False, user_id="bob")
    _add(a._repo, "2026-06-09T10:00", "alice's musing", user="alice")
    assert "alice" in (a._thoughts_block() or "")
    assert b._thoughts_block() is None  # bob's diary block never shows alice's thought


def test_block_off_when_disabled(tmp_path):
    core = _core(tmp_path, thoughts_enabled=False)
    _add(core._repo, "2026-06-09T10:00", "x")
    assert core._thoughts_block() is None


def test_window_is_configurable(tmp_path):
    core = _core(tmp_path, thoughts_window_h=2)  # only the last 2 hours
    _add(core._repo, "2026-06-09T08:00", "morning")  # 12h ago
    _add(core._repo, "2026-06-09T19:00", "an hour ago")
    block = core._thoughts_block()
    assert "an hour ago" in block and "morning" not in block


# --- the soft mood nudge --------------------------------------------------
def test_mood_request_takes_recent_thoughts():
    _, msgs = mood_request("natal", "2026-06-09", thoughts="- хочеться творити")
    assert "хочеться творити" in msgs[0]["content"]

"""Proactive nudge — silent %think on the timer + graduate-to-spoken (v0.12, LUMI-050)."""

from datetime import UTC, datetime, timedelta

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from core.thoughts import should_graduate
from state.local_store import JsonRepository

_NOW = datetime(2026, 6, 9, 14, 0, tzinfo=UTC)


def _core(tmp_path, **kw):
    core = Core(
        llm=MockLLMClient("мимохідь подумала про море\nЕМОЦІЯ: calm"),
        repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m",
        clock=fixed_clock(_NOW), mood_enabled=False,
        thoughts_interval_s=600, thoughts_cap=3, **kw,
    )
    return core, core.start_session()


# --- should_graduate (deterministic ratio) --------------------------------
def test_should_graduate_honors_ratio():
    assert should_graduate(5, 0.0) is False  # never
    assert should_graduate(5, 1.0) is True   # always
    assert should_graduate(10, 0.5) is True   # 10 % 100 = 10 < 50
    assert should_graduate(70, 0.5) is False  # 70 < 50 → False


# --- tick_think: the timer + cap + quiet hours ----------------------------
def test_fires_after_the_interval(tmp_path):
    core, s = _core(tmp_path)
    idle = _NOW - timedelta(seconds=601)
    t = core.tick_think(s, idle, _NOW, rng_seed=1)
    assert t is not None and t.kind == "think"  # fired


def test_not_due_before_the_interval(tmp_path):
    core, s = _core(tmp_path)
    fresh = _NOW - timedelta(seconds=60)
    assert core.tick_think(s, fresh, _NOW) is None  # too soon


def test_quiet_hours_suppress(tmp_path):
    # the proactive think uses its OWN quiet window (independent of the nudge's)
    core, s = _core(tmp_path, thoughts_quiet_hours=(13, 15))  # 14:00 is inside
    idle = _NOW - timedelta(seconds=601)
    assert core.tick_think(s, idle, _NOW) is None


def test_per_session_cap(tmp_path):
    core, s = _core(tmp_path)  # cap=3
    idle = _NOW - timedelta(seconds=601)
    fired = [core.tick_think(s, idle, _NOW, rng_seed=i) for i in range(5)]
    assert sum(t is not None for t in fired) == 3  # capped at 3 per session
    # a new session resets the cap
    s2 = core.start_session()
    assert core.tick_think(s2, idle, _NOW) is not None


def test_silent_vs_spoken_ratio(tmp_path):
    core_s, s = _core(tmp_path, thoughts_spoken_ratio=1.0)
    spoken = core_s.tick_think(s, _NOW - timedelta(seconds=601), _NOW)
    assert spoken is not None and spoken.spoken is True  # graduated

    core_q, s2 = _core(tmp_path / "b", thoughts_spoken_ratio=0.0)
    silent = core_q.tick_think(s2, _NOW - timedelta(seconds=601), _NOW)
    assert silent is not None and silent.spoken is False  # silent, but still recorded


def test_disabled_never_fires(tmp_path):
    core, s = _core(tmp_path, thoughts_enabled=False)
    assert core.tick_think(s, _NOW - timedelta(seconds=601), _NOW) is None


def test_tick_think_passes_kind_and_topic(tmp_path):
    # B = free-muse (kind/topic default); A = a seed from the menu (kind+topic).
    core, s = _core(tmp_path)
    idle = _NOW - timedelta(seconds=601)
    free = core.tick_think(s, idle, _NOW, rng_seed=1)  # B
    assert free is not None and free.kind == "think" and "topic" not in free.seeds
    seeded = core.tick_think(s, idle, _NOW, rng_seed=3, kind="wonder", topic="море")  # A
    assert seeded is not None and seeded.kind == "wonder" and "topic" in seeded.seeds

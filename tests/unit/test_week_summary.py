"""Per-week (Mon–Sun) consolidation (date-based recall) — week digests built from session summaries."""

from datetime import UTC, datetime

from core.agent import Core, _monday_of
from core.clock import fixed_clock
from core.llm import MockLLMClient
from core.memory import week_summary_request
from core.repository import ShortSummary, WeekSummary
from state.local_store import JsonRepository

# 2026-06-08 is a Monday → its week is 2026-06-08 (Mon) … 2026-06-14 (Sun).
_NOON_JUN10 = fixed_clock(datetime(2026, 6, 10, 12, 0, tzinfo=UTC))  # a Wednesday


def _core(tmp_path, llm, clock=_NOON_JUN10):
    return Core(
        llm=llm, repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m",
        clock=clock, mood_enabled=False, biorhythms_enabled=False, cycle_enabled=False,
    )


def _ss(sid, summary, ts):
    return ShortSummary("owner", sid, summary, "gist-unused", ts)


def test_monday_of_returns_the_weeks_monday():
    assert _monday_of("2026-06-08") == "2026-06-08"  # Monday → itself
    assert _monday_of("2026-06-10") == "2026-06-08"  # Wednesday → that Monday
    assert _monday_of("2026-06-14") == "2026-06-08"  # Sunday → that Monday
    assert _monday_of("2026-06-15") == "2026-06-15"  # next Monday


def test_week_summary_request_lists_the_session_summaries():
    system, msgs = week_summary_request(["підсумок А", "підсумок Б"])
    assert "тиждень" in system.lower()
    assert "підсумок А" in msgs[0]["content"] and "підсумок Б" in msgs[0]["content"]


# --- store ----------------------------------------------------------------
def test_week_summary_upsert_window_and_isolation(tmp_path):
    repo = JsonRepository(tmp_path / "s.json")
    repo.set_week_summary(WeekSummary("owner", "2026-06-01", "тиж1", 3, "t"))
    repo.set_week_summary(WeekSummary("owner", "2026-06-08", "тиж2", 5, "t"))
    repo.set_week_summary(WeekSummary("owner", "2026-06-08", "тиж2-новий", 7, "t2"))  # upsert
    assert repo.get_week_summary("owner", "2026-06-08").summary == "тиж2-новий"
    assert [w.week_start for w in repo.week_summaries_since("owner", "2026-06-08")] == ["2026-06-08"]
    assert repo.week_summaries_since("bob", "2026-06-01") == []  # isolation


def test_week_summary_survives_a_reload(tmp_path):
    p = tmp_path / "s.json"
    JsonRepository(p).set_week_summary(WeekSummary("owner", "2026-06-08", "р1\nр2", 4, "t"))
    reloaded = JsonRepository(p).get_week_summary("owner", "2026-06-08")
    assert reloaded.summary == "р1\nр2" and reloaded.count == 4


# --- ensure_week_summaries ------------------------------------------------
def test_ensure_consolidates_a_week_keyed_by_monday(tmp_path):
    core = _core(tmp_path, MockLLMClient("Підсумок тижня."))
    core._repo.add_summary(_ss("a", "розмова пн", "2026-06-08T09:00:00+00:00"))  # Mon
    core._repo.add_summary(_ss("b", "розмова ср", "2026-06-10T09:00:00+00:00"))  # Wed, same week
    core.ensure_week_summaries()
    ws = core._repo.get_week_summary("owner", "2026-06-08")  # keyed by the Monday
    assert ws is not None and ws.summary == "Підсумок тижня." and ws.count == 2
    content = core._llm.calls[0]["messages"][0]["content"]
    assert "розмова пн" in content and "розмова ср" in content


def test_ensure_week_is_count_based(tmp_path):
    core = _core(tmp_path, MockLLMClient("підсумок"))
    core._repo.add_summary(_ss("a", "розмова", "2026-06-08T09:00:00+00:00"))
    core.ensure_week_summaries()
    core.ensure_week_summaries()  # count unchanged → no second call
    assert len(core._llm.calls) == 1
    core._repo.add_summary(_ss("b", "ще розмова", "2026-06-09T09:00:00+00:00"))  # same week
    core.ensure_week_summaries()  # count changed → regenerate
    assert len(core._llm.calls) == 2 and core._repo.get_week_summary("owner", "2026-06-08").count == 2

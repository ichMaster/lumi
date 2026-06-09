"""Per-day consolidation (v0.9.x) — ≤4-row daily digests built from a day's gists."""

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from core.memory import clamp_day_summary, day_summary_request
from core.repository import DaySummary, ShortSummary
from state.local_store import JsonRepository

_NOON_JUN8 = fixed_clock(datetime(2026, 6, 8, 12, 0, tzinfo=UTC))


def _core(tmp_path, llm, clock=_NOON_JUN8):
    return Core(
        llm=llm, repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m",
        clock=clock, mood_enabled=False, biorhythms_enabled=False, cycle_enabled=False,
    )


def _ss(sid, gist, ts):
    return ShortSummary("owner", sid, f"detail {sid}", gist, ts)


# --- helpers --------------------------------------------------------------
def test_day_summary_request_lists_the_gists():
    system, msgs = day_summary_request(["перша замітка", "друга"])
    assert "день" in system.lower()
    assert "перша замітка" in msgs[0]["content"] and "друга" in msgs[0]["content"]


def test_clamp_day_summary_keeps_at_most_4_rows_and_strips_bullets():
    out = clamp_day_summary("- one\n- two\n• three\nfour\nfive\nsix")
    assert out.splitlines() == ["one", "two", "three", "four"]


# --- store ----------------------------------------------------------------
def test_day_summary_upsert_window_and_isolation(tmp_path):
    repo = JsonRepository(tmp_path / "s.json")
    repo.set_day_summary(DaySummary("owner", "2026-06-05", "д5", "t"))
    repo.set_day_summary(DaySummary("owner", "2026-06-07", "д7", "t"))
    repo.set_day_summary(DaySummary("owner", "2026-06-07", "д7-новий", "t2"))  # upsert by date
    assert repo.get_day_summary("owner", "2026-06-07").summary == "д7-новий"
    assert [d.date for d in repo.day_summaries_since("owner", "2026-06-06")] == ["2026-06-07"]
    assert repo.day_summaries_since("bob", "2026-06-01") == []  # isolation


def test_day_summary_survives_a_reload(tmp_path):
    p = tmp_path / "s.json"
    JsonRepository(p).set_day_summary(DaySummary("owner", "2026-06-06", "рядок1\nрядок2", "t"))
    assert JsonRepository(p).get_day_summary("owner", "2026-06-06").summary == "рядок1\nрядок2"


# --- ensure_day_summaries -------------------------------------------------
def test_ensure_consolidates_a_completed_day_from_its_gists(tmp_path):
    core = _core(tmp_path, MockLLMClient("Підсумок дня 6."))
    core._repo.add_summary(_ss("a", "gist a", "2026-06-06T09:00:00+00:00"))
    core._repo.add_summary(_ss("b", "gist b", "2026-06-06T18:00:00+00:00"))
    core._repo.add_summary(_ss("t", "gist today", "2026-06-08T10:00:00+00:00"))  # today
    core.ensure_day_summaries()
    # Jun 6 (completed) consolidated from its gists; today (Jun 8) is NOT consolidated.
    assert core._repo.get_day_summary("owner", "2026-06-06").summary == "Підсумок дня 6."
    assert core._repo.get_day_summary("owner", "2026-06-08") is None
    content = core._llm.calls[0]["messages"][0]["content"]
    assert "gist a" in content and "gist b" in content and "gist today" not in content


def test_ensure_is_idempotent(tmp_path):
    core = _core(tmp_path, MockLLMClient("підсумок"))
    core._repo.add_summary(_ss("a", "gist a", "2026-06-06T09:00:00+00:00"))
    core.ensure_day_summaries()
    core.ensure_day_summaries()  # already has it → no second call
    assert len(core._llm.calls) == 1


def test_ensure_caps_a_day_at_4_rows(tmp_path):
    core = _core(tmp_path, MockLLMClient("р1\nр2\nр3\nр4\nр5\nр6"))  # model overshoots
    core._repo.add_summary(_ss("a", "g", "2026-06-06T09:00:00+00:00"))
    core.ensure_day_summaries()
    assert core._repo.get_day_summary("owner", "2026-06-06").summary.splitlines() == [
        "р1", "р2", "р3", "р4",
    ]


def test_ensure_skips_days_without_gists(tmp_path):
    core = _core(tmp_path, MockLLMClient("підсумок"))
    core._repo.add_summary(_ss("a", "", "2026-06-06T09:00:00+00:00"))  # empty gist (old record)
    core.ensure_day_summaries()
    assert core._llm.calls == [] and core._repo.get_day_summary("owner", "2026-06-06") is None

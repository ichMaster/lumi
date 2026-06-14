"""Token-usage ledger + cost report (core/usage.py) and the Core session-close hook."""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from core.usage import (
    UsageRecord,
    append_record,
    load_records,
    pricing_for,
    render_report,
)
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 6, 14, 20, 0, tzinfo=UTC))


def _rec(**kw) -> UsageRecord:
    base = dict(
        session_id="sess1234abcd", user_id="owner", model="claude-opus-4-8",
        started_at="2026-06-14T20:00:00+00:00", ended_at="2026-06-14T20:10:00+00:00",
        turns=2, input=1000, output=500, cache_read=2000, cache_write=100, cache_ttl="5m",
    )
    base.update(kw)
    return UsageRecord(**base)


# --- pricing ---------------------------------------------------------------------------------------
def test_pricing_exact_prefix_family_and_default():
    assert pricing_for("claude-opus-4-8").input == 5.0
    assert pricing_for("claude-opus-4-8[1m]").output == 25.0       # prefix match past a suffix
    assert pricing_for("claude-haiku-4-5-20251001").input == 1.0   # family/prefix
    assert pricing_for("some-future-model").input == 5.0           # default estimate


def test_cost_usd_math_and_cache_ttl_multiplier():
    # 1M input on opus = $5; cache read = 10% = $0.50; cache write 1h = 2× = $10; output = $25.
    assert _rec(input=1_000_000, output=0, cache_read=0, cache_write=0).cost_usd == 5.0
    assert _rec(input=0, output=0, cache_read=1_000_000, cache_write=0).cost_usd == 0.5
    assert _rec(input=0, output=0, cache_read=0, cache_write=1_000_000, cache_ttl="1h").cost_usd == 10.0
    assert _rec(input=0, output=0, cache_read=0, cache_write=1_000_000, cache_ttl="5m").cost_usd == 6.25
    assert _rec(input=0, output=1_000_000, cache_read=0, cache_write=0).cost_usd == 25.0


# --- ledger ----------------------------------------------------------------------------------------
def test_ledger_roundtrip_and_skips_corrupt(tmp_path):
    ledger = tmp_path / "u.jsonl"
    append_record(ledger, _rec(session_id="a"))
    append_record(ledger, _rec(session_id="b", turns=5))
    ledger.write_text(ledger.read_text() + "not json\n\n", encoding="utf-8")  # junk line tolerated
    recs = load_records(ledger)
    assert [r.session_id for r in recs] == ["a", "b"]
    assert recs[1].turns == 5
    assert load_records(tmp_path / "missing.jsonl") == []


# --- report ----------------------------------------------------------------------------------------
def test_render_report_aggregates_by_period():
    records = [
        _rec(started_at="2026-06-14T20:00:00+00:00", input=1000),
        _rec(started_at="2026-06-14T22:00:00+00:00", input=3000),  # same day → day row sums
        _rec(started_at="2026-05-02T09:00:00+00:00", input=500),   # different month/week/day
    ]
    md = render_report(records, generated_at="2026-06-14T20:30:00")
    assert "# Lumi — token usage" in md
    for section in ("## By month", "## By week", "## By day", "## Recent sessions"):
        assert section in md
    assert "2026-06" in md and "2026-05" in md          # both months present
    assert "2026-06-14" in md and "2026-05-02" in md     # both days present
    # the 2026-06-14 day row aggregates both sessions → 4,000 input tokens
    day_row = next(line for line in md.splitlines() if "2026-06-14 |" in line)
    assert "4,000" in day_row and "| 2 |" in day_row     # 2 sessions that day


# --- Core hook -------------------------------------------------------------------------------------
def _core(tmp_path, *, ledger=None, report=None, ttl="1h"):
    return Core(
        llm=MockLLMClient("ок\nЕМОЦІЯ: calm"),
        repository=JsonRepository(tmp_path / "s.json"),
        canon="C", model="claude-opus-4-8", clock=_CLK, mood_enabled=False,
        usage_ledger_path=ledger, usage_report_path=report, usage_cache_ttl=ttl,
    )


def test_core_writes_usage_record_and_report_on_close(tmp_path):
    ledger, report = tmp_path / "usage-ledger.jsonl", tmp_path / "usage-report.md"
    core = _core(tmp_path, ledger=ledger, report=report, ttl="1h")
    s = core.start_session()
    core.totals.turns, core.totals.input_tokens = 3, 1000
    core.totals.output_tokens, core.totals.cache_read_tokens, core.totals.cache_write_tokens = 500, 2000, 100
    core.end_session(s)

    recs = load_records(ledger)
    assert len(recs) == 1
    r = recs[0]
    assert (r.turns, r.input, r.output, r.cache_read, r.cache_write) == (3, 1000, 500, 2000, 100)
    assert r.model == "claude-opus-4-8" and r.cache_ttl == "1h"
    assert report.exists() and "token usage" in report.read_text().lower()


def test_core_session_deltas_are_per_session(tmp_path):
    ledger = tmp_path / "usage-ledger.jsonl"
    core = _core(tmp_path, ledger=ledger)
    s1 = core.start_session()
    core.totals.input_tokens = 1000
    core.end_session(s1)
    s2 = core.start_session()
    core.totals.input_tokens = 1700  # cumulative; this session's delta is 700
    core.end_session(s2)

    recs = load_records(ledger)
    assert [r.input for r in recs] == [1000, 700]


def test_core_skips_empty_session_and_honours_off(tmp_path):
    # off (no paths) → never writes
    off_core = _core(tmp_path)
    so = off_core.start_session()
    off_core.totals.input_tokens = 9
    off_core.end_session(so)
    assert not (tmp_path / "usage-ledger.jsonl").exists()

    # on, but a session with zero usage writes no row
    ledger = tmp_path / "usage-ledger.jsonl"
    core = _core(tmp_path, ledger=ledger)
    core.end_session(core.start_session())
    assert load_records(ledger) == []

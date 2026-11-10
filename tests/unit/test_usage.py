"""Token-usage ledger + cost report (core/usage.py) and the Core session-close hook."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

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


def test_pricing_gemini_models_no_longer_cost_the_opus_default():
    # v1.6.3 LUMI-206: before these rows every gemini call (the WHOLE voice mode) was estimated at
    # the $5/$25 opus default — a ~17–50× overstatement in the ledger and /usage.
    assert pricing_for("gemini-3.1-pro-preview").input == 2.0      # prefix past "-preview"
    assert pricing_for("gemini-3.1-pro-preview").output == 12.0
    assert pricing_for("gemini-2.5-flash").input == 0.30           # the voice tier
    assert pricing_for("gemini-2.5-flash-lite").input == 0.10      # the more-specific prefix wins
    assert pricing_for("gemini-3.5-flash").output == 9.0
    assert pricing_for("gemini-9.9-flash-lite-preview").input == 0.10  # family fallback: flash-lite
    assert pricing_for("gemini-9.9-flash-preview").input == 0.30       # family fallback: flash


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


def test_render_report_includes_cost_breakdown_and_cache_saving():
    # opus: 1M input=$5, 1M output=$25, 2M cache-read=$1.00, 100k cache-write(1h)=$1.00
    records = [_rec(input=1_000_000, output=1_000_000, cache_read=2_000_000, cache_write=100_000, cache_ttl="1h")]
    md = render_report(records, generated_at="2026-06-16")
    assert "## Cost breakdown" in md
    for bucket in ("input", "output", "cache read", "cache write"):
        assert f"| {bucket} |" in md
    cw_row = next(line for line in md.splitlines() if line.startswith("| cache write |"))
    assert "$10.00" in cw_row              # blended rate for 1h cache write (2× input)
    assert "saved" in md                   # the net-cache benefit line


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



# --- v2.10 LUMI-252: the prices as data (core/pricing.yaml) ---------------------------------------------------

_OLD_TABLE = {  # core/usage.py PRICING before v2.10 — every id must still resolve to the same price
    "claude-opus-4-8": (5.0, 25.0), "claude-opus-4-7": (5.0, 25.0), "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0), "claude-sonnet-4-6": (3.0, 15.0), "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0), "gemini-3.1-pro": (2.0, 12.0), "gemini-3.7-flash": (0.75, 3.75),
    "gemini-3.6-flash": (0.75, 3.75), "gemini-3.5-flash": (1.5, 9.0), "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
}


@pytest.mark.parametrize("model, price", sorted(_OLD_TABLE.items()))
def test_pricing_yaml_keeps_every_old_price(model, price):
    from core.usage import pricing_for

    p = pricing_for(model)
    assert (p.input, p.output) == price


@pytest.mark.parametrize("model, price", [
    ("claude-haiku-4-5-20251001", (1.0, 5.0)),          # a dated id: the prefix
    ("gemini-3.1-pro-preview", (2.0, 12.0)),
    ("gemini-2.5-flash-lite-preview", (0.10, 0.40)),    # the more specific prefix wins over gemini-2.5-flash
    ("claude-sonnet-5", (3.0, 15.0)),                   # added in v2.10 (the intro price ended Sep 1, 2026)
    ("claude-fable-5", (10.0, 50.0)),                   # added in v2.10
    ("some-new-model", (5.0, 25.0)),                    # unknown → the opus-tier default
])
def test_pricing_lookup_order(model, price):
    from core.usage import pricing_for

    p = pricing_for(model)
    assert (p.input, p.output) == price


def test_the_cache_multipliers_come_from_the_file():
    from core.usage import pricing_for

    p = pricing_for("claude-opus-4-8")
    assert p.cache_read == pytest.approx(0.5) and p.cache_write("5m") == pytest.approx(6.25)
    assert p.cache_write("1h") == pytest.approx(10.0)


@pytest.mark.parametrize("text", [None, "not: [valid", "models: {}\ndefault: {input: 1, output: 2}\n",
                                  "models: {x: {input: nope}}\n"])
def test_a_missing_or_malformed_pricing_file_falls_back_to_the_builtin_table(tmp_path, text, caplog):
    from core.usage import _BUILTIN_DEFAULT, _BUILTIN_PRICING, load_pricing

    path = tmp_path / "pricing.yaml"
    if text is not None:
        path.write_text(text, encoding="utf-8")
    models, default, cache = load_pricing(path)
    assert models == _BUILTIN_PRICING and default == _BUILTIN_DEFAULT and cache["read"] == 0.10
    assert any("built-in prices hold" in r.getMessage() for r in caplog.records)

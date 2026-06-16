"""Cost analysis report — core/cost_report.py: tokens + cost by session × activity × operation."""
from __future__ import annotations

from core.cache_log import CacheEvent
from core.cost_report import render_cost_report

# CacheEvent(ts, kind, cache_read, cache_write, input, output, gap_s, cause, model, session_id)
_EVENTS = [
    CacheEvent("2026-06-16T08:00:00", "reply", 20000, 5000, 4000, 200, 12.0, "moved", "claude-opus-4-8", "sess-aaaa1111"),
    CacheEvent("2026-06-16T08:01:00", "tool", 24000, 0, 500, 90, 1.0, "none", "claude-opus-4-8", "sess-aaaa1111"),
    CacheEvent("2026-06-16T09:00:00", "think", 3000, 0, 0, 0, 3600.0, "none", "claude-opus-4-8", "sess-bbbb2222"),
]


def test_cost_report_structure():
    md = render_cost_report(_EVENTS, generated_at="2026-06-16", ttl="1h")
    assert "# Lumi — cost analysis" in md
    assert "## All sessions" in md and "## Per session" in md
    assert "**By operation:**" in md and "Share" in md
    # one block per session, in first-seen order
    assert "### Session `sess-aaa`" in md and "### Session `sess-bbb`" in md
    allsec = md.split("## All sessions")[1].split("## Per session")[0]
    assert "| reply |" in allsec and "| tool |" in allsec and "| think |" in allsec
    assert "**TOTAL**" in allsec


def test_cost_report_per_operation_math():
    # reply (opus, 1h): input 4000×$5 + output 200×$25 + cache-read 20000×$0.5 + cache-write 5000×$10
    #   = 0.02 + 0.005 + 0.01 + 0.05 = $0.0850 (per 1M)
    md = render_cost_report(_EVENTS, generated_at="2026-06-16", ttl="1h")
    reply_row = next(line for line in md.splitlines() if line.startswith("| reply |"))
    assert "$0.0200" in reply_row and "$0.0050" in reply_row  # input + output cost columns
    assert "$0.0100" in reply_row and "$0.0500" in reply_row  # cache-read + cache-write cost columns
    assert "$0.0850" in reply_row                              # the activity total


def test_cost_report_per_session_isolated_and_legacy():
    # session B (think only) totals just the think cost; events without a session id group under (legacy)
    legacy = [CacheEvent("2026-06-16T07:00:00", "reply", 0, 0, 1000, 50, None, "first", "claude-opus-4-8", "")]
    md = render_cost_report(_EVENTS + legacy, generated_at="2026-06-16", ttl="1h")
    b_block = md.split("### Session `sess-bbb`")[1].split("### Session `(legacy)`")[0]
    assert "| think |" in b_block and "| reply |" not in b_block  # B has only think
    assert "### Session `(legacy)`" in md

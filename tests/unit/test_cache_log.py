"""Per-call cache monitor — core/cache_log.py (module) + the Core hook (per-channel attribution)."""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.cache_log import (
    CacheEvent,
    append_event,
    classify,
    load_events,
    render_cache_report,
    ttl_seconds,
)
from core.llm import MockLLMClient, ResponseStats
from state.local_store import JsonRepository


# --- module ----------------------------------------------------------------------------------------
def test_classify_write_cause():
    ttl = 3600
    assert classify(0, None, ttl) == "none"       # no write (a read or uncached call)
    assert classify(0, 100, ttl) == "none"
    assert classify(100, None, ttl) == "first"    # first call of this channel
    assert classify(100, 7200, ttl) == "expired"  # gap > TTL → the entry timed out
    assert classify(100, 60, ttl) == "changed"    # warm, but the prefix moved


def test_ttl_seconds():
    assert ttl_seconds("1h") == 3600 and ttl_seconds("5m") == 300 and ttl_seconds("?") == 300


def test_event_ledger_roundtrip(tmp_path):
    p = tmp_path / "c.jsonl"
    append_event(p, CacheEvent("t1", "reply", 100, 0, 10, 5, 12.0, "none"))
    append_event(p, CacheEvent("t2", "think", 0, 2000, 5, 3, None, "first"))
    evs = load_events(p)
    assert [e.kind for e in evs] == ["reply", "think"] and evs[1].cause == "first"
    assert load_events(tmp_path / "missing.jsonl") == []


def test_render_groups_by_channel_and_cause():
    events = [
        CacheEvent("t", "reply", 20000, 0, 100, 50, 12.0, "none"),
        CacheEvent("t", "reply", 20000, 0, 100, 50, 12.0, "none"),
        CacheEvent("t", "reply", 0, 22000, 100, 50, None, "first"),
        CacheEvent("t", "think", 0, 22000, 100, 50, 7200.0, "expired"),
        CacheEvent("t", "think", 0, 22000, 100, 50, 7200.0, "expired"),
    ]
    md = render_cache_report(events, generated_at="2026-06-16", ttl="1h")
    assert "## By channel" in md and "## Writes by cause" in md
    think_row = next(line for line in md.splitlines() if line.startswith("| think"))
    assert "2 (2 / 0 / 0)" in think_row              # think: 2 writes, both expired
    reply_row = next(line for line in md.splitlines() if line.startswith("| reply"))
    assert "1 (0 / 1 / 0)" in reply_row              # reply: 1 write, first-of-channel
    assert "**expired** (idle > TTL): 2" in md


# --- Core hook -------------------------------------------------------------------------------------
class _Clock:
    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt


def test_core_logs_cache_events_by_channel_with_attribution(tmp_path):
    clk = _Clock(datetime(2026, 6, 16, 8, 0, tzinfo=UTC))
    log, rep = tmp_path / "cache-log.jsonl", tmp_path / "cache-report.md"
    core = Core(
        llm=MockLLMClient(states={"reply": "ок", "emotion": "calm", "intensity": 0.5}),
        repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m", clock=clk,
        mood_enabled=False, cache_monitor=True, cache_log_path=log, cache_report_path=rep,
        usage_cache_ttl="1h",
    )

    def _call(kind, *, write):
        core._llm.last_stats = ResponseStats(model="m", latency_ms=5,
                                             cache_read_tokens=0 if write else 20000,
                                             cache_write_tokens=write)
        core._accumulate_stats(turn=(kind == "reply"), kind=kind)

    _call("reply", write=22000)                                   # first reply write
    clk.dt = datetime(2026, 6, 16, 10, 30, tzinfo=UTC)            # +2.5h
    _call("think", write=22000)                                  # think: first call of its channel
    clk.dt = datetime(2026, 6, 16, 13, 30, tzinfo=UTC)            # +3h gap (> 1h TTL)
    _call("think", write=22000)                                  # think: expired
    clk.dt = datetime(2026, 6, 16, 13, 40, tzinfo=UTC)            # +10m (warm)
    _call("think", write=22000)                                  # think: changed

    evs = load_events(log)
    assert [e.kind for e in evs] == ["reply", "think", "think", "think"]
    assert [e.cause for e in evs] == ["first", "first", "expired", "changed"]

    core._render_cache_report()
    text = rep.read_text(encoding="utf-8")
    assert "By channel" in text and "think" in text and "reply" in text


def test_core_logs_per_round_tool_and_reply_for_a_file_turn(tmp_path):
    # A file-tool turn = 2 tool calls + the answer → 3 per-round events (tool, tool, reply), not 1 sum.
    root = tmp_path / "files" / "owner"
    root.mkdir(parents=True)
    (root / "notes.md").write_text("a\nРозділ 4: оплата\nb\n", encoding="utf-8")
    log = tmp_path / "cache-log.jsonl"
    mock = MockLLMClient(states={"reply": "ок", "emotion": "calm", "intensity": 0.5},
                         tool_script=[("find_in_file", {"path": "notes.md", "query": "Розділ 4"}),
                                      ("read_file", {"path": "notes.md", "start_line": 2, "line_count": 1})])
    core = Core(
        llm=mock, repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m",
        clock=_Clock(datetime(2026, 6, 16, 8, 0, tzinfo=UTC)),
        mood_enabled=False, closeness_enabled=False, thoughts_enabled=False,
        cache_monitor=True, cache_log_path=log, cache_report_path=tmp_path / "r.md",
        file_tool_enabled=True, files_dir=tmp_path / "files", file_read_lines=10,
    )
    core.reply("прочитай розділ про оплату", core.start_session())
    assert [e.kind for e in load_events(log)] == ["tool", "tool", "reply"]  # per round, split out


def test_render_includes_per_activity_cost_table():
    events = [
        CacheEvent("t", "reply", 20000, 0, 4000, 200, 12.0, "none", "claude-opus-4-8"),
        CacheEvent("t", "tool", 24000, 5000, 500, 90, 1.0, "changed", "claude-opus-4-8"),
        CacheEvent("t", "tool", 24000, 5000, 600, 90, 1.0, "changed", "claude-opus-4-8"),
        CacheEvent("t", "think", 0, 0, 3000, 400, 3600.0, "none", "claude-opus-4-8"),
    ]
    md = render_cache_report(events, generated_at="2026-06-16", ttl="1h")
    assert "## By activity (tokens & cost)" in md
    for activity in ("reply", "tool", "think"):
        assert f"| {activity} |" in md
    assert "Est. cost" in md and "$" in md


def test_core_logs_nothing_when_monitor_off(tmp_path):
    core = Core(
        llm=MockLLMClient(states={"reply": "ок", "emotion": "calm", "intensity": 0.5}),
        repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m",
        clock=_Clock(datetime(2026, 6, 16, 8, 0, tzinfo=UTC)),
        mood_enabled=False, cache_monitor=False,
    )
    core._llm.last_stats = ResponseStats(model="m", latency_ms=5, cache_write_tokens=22000)
    core._accumulate_stats(turn=True, kind="reply")
    assert not (tmp_path / "cache-log.jsonl").exists()

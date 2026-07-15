"""Per-call cache monitor — core/cache_log.py (module) + the Core hook (per-channel attribution)."""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.cache_log import (
    CacheEvent,
    append_event,
    classify,
    diff_sections,
    load_events,
    prefix_sections,
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
    assert classify(100, 60, ttl, prefix_changed=True) == "moved"      # warm + the prefix actually changed
    assert classify(100, 60, ttl, prefix_changed=False) == "evicted"   # warm + identical → cache dropped early


def test_prefix_sections_and_diff():
    p1 = "ти лілі\n\n# Памʼять про цю людину\n\nфакти\n\n# Настрій дня\n\nспокій"
    secs = prefix_sections(p1)
    assert set(secs) == {"(canon)", "# Памʼять про цю людину", "# Настрій дня"}
    assert prefix_sections("") == {}
    assert diff_sections(None, secs) == (True, "")                 # first call of the group
    assert diff_sections(secs, secs) == (False, "")                # identical → not changed
    p2 = p1.replace("спокій", "радість")                           # only the mood section moves
    changed, which = diff_sections(secs, prefix_sections(p2))
    assert changed and which == "# Настрій дня"


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
    assert "2 (0 / 2 / 0 / 0)" in think_row          # think: 2 writes, both expired (first/expired/moved/evicted)
    reply_row = next(line for line in md.splitlines() if line.startswith("| reply"))
    assert "1 (1 / 0 / 0 / 0)" in reply_row          # reply: 1 write, first-of-channel
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
    assert [e.cause for e in evs] == ["first", "first", "expired", "moved"]  # no prefix set → defaults to moved

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


def test_session_close_calls_are_tagged(tmp_path):
    log = tmp_path / "cache-log.jsonl"
    core = Core(
        llm=MockLLMClient(states={"reply": "ок", "emotion": "calm", "intensity": 0.5}),
        repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m",
        clock=_Clock(datetime(2026, 6, 16, 8, 0, tzinfo=UTC)),
        mood_enabled=False, closeness_enabled=False, thoughts_enabled=False,
        cache_monitor=True, cache_log_path=log, cache_report_path=tmp_path / "r.md",
    )
    s = core.start_session()
    core.reply("привіт", s)
    core.end_session(s)  # → the wrap-up summary + facts extraction, tagged "session-close"
    assert "session-close" in [e.kind for e in load_events(log)]


def test_render_has_per_session_tokens_and_cost_tables():
    events = [
        CacheEvent("2026-06-16T08:00:00", "reply", 20000, 0, 4000, 200, 12.0, "none", "claude-opus-4-8", "sess-aaaa1111"),
        CacheEvent("2026-06-16T08:01:00", "tool", 24000, 5000, 500, 90, 1.0, "evicted", "claude-opus-4-8", "sess-aaaa1111"),
        CacheEvent("2026-06-16T09:00:00", "think", 0, 0, 3000, 400, 3600.0, "none", "claude-opus-4-8", "sess-bbbb2222"),
    ]
    md = render_cache_report(events, generated_at="2026-06-16", ttl="1h")
    assert "## Per session — by activity (tokens & cost)" in md
    assert "### Session `sess-aaa`" in md and "### Session `sess-bbb`" in md  # one block per session
    a_block = md.split("### Session `sess-aaa`")[1].split("### Session `sess-bbb`")[0]
    # each session block has BOTH a tokens table and a cost table
    assert "**Tokens**" in a_block and "**Cost**" in a_block
    assert "| reply |" in a_block and "| tool |" in a_block and "| think |" not in a_block
    # the tokens table shows token counts (reply input = 4,000); the cost table shows $ (reply input = $0.0200)
    assert "4,000" in a_block and "$0.0200" in a_block


def test_render_groups_by_session_overview():
    events = [
        CacheEvent("2026-06-16T08:00:00", "reply", 20000, 0, 100, 50, 12.0, "none", "claude-opus-4-8", "sess-aaaa1111"),
        CacheEvent("2026-06-16T08:01:00", "tool", 24000, 5000, 100, 50, 1.0, "changed", "claude-opus-4-8", "sess-aaaa1111"),
        CacheEvent("2026-06-16T09:00:00", "reply", 20000, 0, 100, 50, 3600.0, "none", "claude-opus-4-8", "sess-bbbb2222"),
        CacheEvent("2026-06-16T09:05:00", "reply", 0, 22000, 100, 50, None, "first", "claude-opus-4-8", ""),  # legacy (no id)
    ]
    md = render_cache_report(events, generated_at="2026-06-16", ttl="1h")
    assert "## By session (overview)" in md
    assert "sess-aaa" in md and "sess-bbb" in md          # short ids, in first-seen order
    assert "(legacy)" in md                                # pre-field events grouped apart
    a_row = next(line for line in md.splitlines() if line.startswith("| sess-aaa"))
    assert "| 2 |" in a_row and "5,000" in a_row           # session A: 2 calls, 5k written
    assert "Read:Write" in md and "9:1" in a_row           # session A: 44k read / 5k write ≈ 9:1
    assert "Share" in md and "$" in a_row                  # overview carries cost + share


def test_core_stamps_cache_events_with_the_active_session(tmp_path):
    log = tmp_path / "cache-log.jsonl"
    core = Core(
        llm=MockLLMClient(states={"reply": "ок", "emotion": "calm", "intensity": 0.5}),
        repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m",
        clock=_Clock(datetime(2026, 6, 16, 8, 0, tzinfo=UTC)),
        mood_enabled=False, closeness_enabled=False, thoughts_enabled=False,
        cache_monitor=True, cache_log_path=log, cache_report_path=tmp_path / "r.md",
    )
    s = core.start_session()
    core.reply("привіт", s)
    core.end_session(s)
    sids = {e.session_id for e in load_events(log)}
    assert sids == {s.id}  # every event (reply + session-close) carries the real session id


def test_render_has_both_tokens_and_cost_activity_tables():
    # the merged report keeps BOTH a tokens breakdown and a cost ($) breakdown per activity
    events = [
        CacheEvent("t", "reply", 20000, 5000, 4000, 200, 12.0, "moved", "claude-opus-4-8"),
        CacheEvent("t", "tool", 24000, 0, 500, 90, 1.0, "none", "claude-opus-4-8"),
        CacheEvent("t", "think", 3000, 0, 0, 0, 3600.0, "none", "claude-opus-4-8"),
    ]
    md = render_cache_report(events, generated_at="2026-06-16", ttl="1h")
    assert "# Lumi — prompt-cache & cost" in md          # unified report
    assert "## By activity — tokens" in md and "## By activity — cost" in md  # BOTH tables
    assert "**Total cost:**" in md and "cache write" in md  # the by-operation summary line

    # tokens table: operation columns are token counts (reply input = 4,000)
    tok_sec = md.split("## By activity — tokens")[1].split("## By activity — cost")[0]
    tok_reply = next(line for line in tok_sec.splitlines() if line.startswith("| reply |"))
    assert "4,000" in tok_reply and "20,000" in tok_reply  # input + cache-read token counts

    # cost table: operation columns are cost $ — reply input 4000×$5 + output 200×$25 +
    #   cache-rd 20000×$0.5 + cache-wr 5000×$10 = 0.02 + 0.005 + 0.01 + 0.05 = $0.0850
    cost_sec = md.split("## By activity — cost")[1].split("## By session")[0]
    cost_reply = next(line for line in cost_sec.splitlines() if line.startswith("| reply |"))
    assert "$0.0200" in cost_reply and "$0.0050" in cost_reply     # input + output cost columns
    assert "$0.0100" in cost_reply and "$0.0500" in cost_reply     # cache-read + cache-write cost columns
    assert "$0.0850" in cost_reply                                 # the activity total
    assert "**TOTAL**" in cost_sec


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


# --- v1.3 LUMI-185: latency_ms on the record + report median column ------------------------------


def test_latency_ms_round_trips_and_legacy_records_default_none(tmp_path):
    p = tmp_path / "c.jsonl"
    append_event(p, CacheEvent("t1", "reply", 100, 0, 10, 5, 12.0, "none", latency_ms=2300))
    # a legacy line without the field must still load (latency_ms → None)
    p.open("a", encoding="utf-8").write(
        '{"ts":"t2","kind":"reply","cache_read":0,"cache_write":0,"input":1,"output":1,'
        '"gap_s":null,"cause":"none"}\n'
    )
    evs = load_events(p)
    assert evs[0].latency_ms == 2300 and evs[1].latency_ms is None


def test_report_shows_per_channel_median_latency():
    events = [
        CacheEvent("t", "reply", 20000, 0, 100, 50, 12.0, "none", latency_ms=1000),
        CacheEvent("t", "reply", 20000, 0, 100, 50, 12.0, "none", latency_ms=3000),
        CacheEvent("t", "reply", 20000, 0, 100, 50, 12.0, "none", latency_ms=5000),
    ]
    md = render_cache_report(events, generated_at="2026-07-15", ttl="1h")
    assert "Median latency" in md
    reply_row = next(line for line in md.splitlines() if line.startswith("| reply"))
    assert "3.0s" in reply_row  # median of 1000/3000/5000 ms


def test_report_renders_when_no_record_carries_latency():
    # backward-compat: all-legacy events → the column shows '—', never crashes
    events = [CacheEvent("t", "reply", 100, 0, 10, 5, 12.0, "none")]
    md = render_cache_report(events, generated_at="2026-07-15", ttl="1h")
    reply_row = next(line for line in md.splitlines() if line.startswith("| reply"))
    assert reply_row.rstrip().endswith("— |")


# --- v1.3 LUMI-187: explicit-cache observability + continuity report -----------------------------


def test_cache_event_round_trips():
    p_events = [CacheEvent("t", "reply", 12000, 0, 5, 5, None, "none", cache_event="created")]
    md = render_cache_report(p_events, generated_at="2026-07-15", ttl="1h")
    assert "## Explicit cache (Gemini)" in md
    assert "**created**: 1" in md


def test_continuity_table_lists_post_gap_warm_turns():
    events = [
        CacheEvent("2026-07-15T10:00", "reply", 12000, 0, 5, 5, None, "none", cache_event="created", latency_ms=2000),
        # a reply after a 20-min gap that STAYED warm (cache_read ≈ prefix) — the continuity proof
        CacheEvent("2026-07-15T10:20", "reply", 12000, 0, 5, 5, 1200.0, "evicted", latency_ms=2100),
    ]
    md = render_cache_report(events, generated_at="2026-07-15", ttl="1h")
    assert "Continuity" in md
    row = next(line for line in md.splitlines() if "2026-07-15T10:20" in line)
    assert "20m" in row and "12,000" in row and "2.1s" in row


def test_report_has_no_explicit_cache_section_when_feature_never_ran():
    # off / non-Gemini → no cache_event on any record → the section is absent (unchanged report)
    events = [CacheEvent("t", "reply", 100, 0, 5, 5, None, "none")]
    md = render_cache_report(events, generated_at="2026-07-15", ttl="1h")
    assert "Explicit cache (Gemini)" not in md

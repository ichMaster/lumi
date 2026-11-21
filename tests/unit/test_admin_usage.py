"""v5.1 (LUMI-342): the panel's usage and tool rollups and their aggregates — over fixture ledgers, no network.

The fixture: one instance whose ledgers hold three closed days (Nov 7, 8, 9) and today (Nov 10). Every figure below is
counted by hand from the records written here.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from admin import readers as rd
from admin import usage as us
from core.usage import pricing_for

TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 11, 10, 15, 0, tzinfo=TZ)


def _ts(day: int, hour: int = 12) -> str:
    return datetime(2026, 11, day, hour, 0, tzinfo=TZ).isoformat()


def _call(day, kind="reply", origin="turn", channel="window", model="gemini-2.5-flash", inp=1000, out=100,
          read=500, write=0, lat=3000, pre=200, post=10, hour=12):
    return {"ts": _ts(day, hour), "kind": kind, "model": model, "input": inp, "output": out, "cache_read": read,
            "cache_write": write, "latency_ms": lat, "pre_ms": pre, "post_ms": post, "session_id": "s1",
            "origin": origin, "channel": channel}


def _media(day, service="tts", provider="elevenlabs", model="eleven_v4_turbo", units=None, outcome="ok",
           origin="turn", channel="window", mode=None):
    rec = {"ts": _ts(day), "provider": provider, "service": service, "model": model,
           "units": units or {"chars": 2000}, "latency_ms": 900, "outcome": outcome, "origin": origin,
           "channel": channel}
    return rec | ({"mode": mode} if mode else {})


def _tool(day, tool="read_file", outcome="ok", ms=40, origin="turn", hour=12):
    return {"ts": _ts(day, hour), "tool": tool, "outcome": outcome, "ms": ms, "result_chars": 10,
            "turn_id": _ts(day, hour), "session_id": "s1", "origin": origin, "channel": "window",
            "input": "path=notes/a.md"}


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


@pytest.fixture
def world(tmp_path):
    inst = rd.Instance("prod", tmp_path / "prod", "http://192.168.1.197:8765", "lumi-prod")
    data = inst.data
    _write(data / "cache-log.jsonl", [
        _call(7), _call(7, kind="mood", origin="housekeeping:mood", lat=1000, hour=8),
        _call(8, origin="turn", channel="telegram", lat=5000),
        # Nov 9: no model call at all (only a tool) → the cost per day has a gap there
        _call(10, lat=7000), _call(10, kind="think", origin="schedule:catchup", channel="", lat=2000, hour=13),
    ])
    _write(data / "media-ledger.jsonl", [
        _media(7), _media(8, service="stt", provider="deepgram", model="nova-3", units={"audio_s": 30}, mode="batch"),
        _media(10, service="image", provider="google", model="gemini-2.5-flash-image", units={"images": 1}),
        _media(10, provider="mystery", service="tts", model="x", units={"chars": 10}),  # not priced
    ])
    _write(data / "tools.jsonl", [
        _tool(7, ms=40), _tool(7, ms=60), _tool(8, tool="web_lookup", outcome="error:TimeoutError", ms=15000),
        _tool(9, tool="web_lookup", ms=4000), _tool(10, ms=50),
    ])
    rollups = us.Rollups(tmp_path / "admin", [inst], clock=lambda: NOW, ttl_of=lambda i: "5m")
    return rollups, inst


def _flash_cost(inp=1000, out=100, read=500):
    p = pricing_for("gemini-2.5-flash")
    return (inp * p.input + out * p.output + read * p.cache_read) / 1_000_000


def test_a_day_is_closed_once_and_a_rerun_changes_nothing(world):
    rollups, inst = world
    assert rollups.run() == {"prod": 3}  # Nov 7, 8, 9 (today is never closed)
    usage_file = rollups.usage_path("prod").read_text()
    tools_file = rollups.tools_path("prod").read_text()
    assert rollups.closed_through("prod") == "2026-11-09"
    assert rollups.run() == {"prod": 0}
    assert rollups.usage_path("prod").read_text() == usage_file
    assert rollups.tools_path("prod").read_text() == tools_file
    days = {r["day"] for r in map(json.loads, usage_file.splitlines())}
    assert days == {"2026-11-07", "2026-11-08"}  # Nov 9 had no model or media record → nothing (a gap)


def test_a_closed_day_outlives_the_instance_trim(world):
    rollups, inst = world
    rollups.run()
    for name in ("cache-log.jsonl", "media-ledger.jsonl", "tools.jsonl"):  # the instance trims its ledgers
        (inst.data / name).write_text("", encoding="utf-8")
    view = us.usage_view(rollups, [inst], range_name="7d")
    day7 = next(d for d in view["per_day"] if d["day"] == "2026-11-07")
    assert not day7["gap"] and day7["total"] > 0
    tools = us.tools_view(rollups, [inst], range_name="7d")
    assert {t["tool"]: t["calls"] for t in tools["tools"]} == {"read_file": 2, "web_lookup": 2}


def test_rows_older_than_400_days_are_dropped(world, tmp_path):
    rollups, inst = world
    old = {"day": "2025-01-01", "row": "group", "provider": "google", "service": "model", "model": "m", "kind": "reply",
           "origin": "turn", "channel": "window", "calls": 1, "input": 0, "output": 0, "cache_read": 0,
           "cache_write": 0, "units": {}, "cost": 1.0, "unpriced": 0, "saved": 0.0}
    _write(rollups.usage_path("prod"), [old])
    rollups.run()
    assert "2025-01-01" not in rollups.usage_path("prod").read_text()


def test_the_figures_match_a_hand_count(world):
    rollups, inst = world
    rollups.run()
    view = us.usage_view(rollups, [inst], range_name="7d")
    model = 5 * _flash_cost()  # five model calls of the same size (Nov 7 ×2, Nov 8, today ×2)
    tts = 2000 / 1000 * 0.05  # eleven_v4_turbo: $0.05 per 1 000 characters
    stt = 30 / 60 * 0.0043  # nova-3 batch: $0.0043 a minute
    image = 0.039
    by_provider = {b["key"]: b["cost"] for b in view["by_provider"]}
    assert by_provider["google"] == pytest.approx(round(model + image, 4), abs=1e-4)
    assert by_provider["elevenlabs"] == pytest.approx(tts, abs=1e-4)
    assert view["unpriced"] == 1  # the mystery provider
    assert by_provider["deepgram"] == pytest.approx(stt, abs=1e-4)
    by_origin = {b["key"]: b["calls"] for b in view["by_origin"]}
    assert by_origin == {"turn": 7, "housekeeping:mood": 1, "schedule:catchup": 1}
    by_channel = {b["key"]: b["calls"] for b in view["by_channel"]}
    assert by_channel == {"window": 7, "telegram": 1, "—": 1}
    assert view["turns"] == 3  # the owner's replies: Nov 7, 8 and today (the think and the mood are not turns)
    assert view["cache"]["read"] == 5 * 500


def test_a_day_without_data_is_a_gap_not_zero(world):
    rollups, inst = world
    rollups.run()
    view = us.usage_view(rollups, [inst], range_name="7d")
    by_day = {d["day"]: d for d in view["per_day"]}
    assert by_day["2026-11-09"]["gap"] is True
    assert by_day["2026-11-05"]["gap"] is True  # before the ledgers began
    assert by_day["2026-11-08"]["gap"] is False
    lat = {d["day"]: d for d in view["latency"]}
    assert lat["2026-11-09"]["gap"] is True and lat["2026-11-08"]["median"] == 5210.0  # 200 + 5000 + 10


def test_the_month_and_its_forecast(world):
    rollups, inst = world
    rollups.run()
    view = us.usage_view(rollups, [inst], range_name="month")
    month = view["month"]
    assert month["elapsed"] == 10 and month["days"] == 30
    assert month["forecast"] == pytest.approx(month["to_date"] * 3, abs=1e-3)  # 10 days → 30 days at the same pace
    future = [d for d in view["per_day"] if d.get("future")]
    assert len(future) == 20 and future[0]["day"] == "2026-11-11"
    assert month["previous_label"] == "October" and month["previous"] == 0


def test_the_cache_saving_is_the_read_tokens_at_input_minus_read_price(world):
    rollups, inst = world
    rollups.run()
    p = pricing_for("gemini-2.5-flash")
    view = us.usage_view(rollups, [inst], range_name="7d")
    assert view["cache"]["saved"] == pytest.approx(5 * 500 * (p.input - p.cache_read) / 1_000_000, abs=1e-4)


def test_the_tools_table_and_one_tool(world):
    rollups, inst = world
    rollups.run()
    view = us.tools_view(rollups, [inst], range_name="7d", tool="web_lookup")
    rows = {t["tool"]: t for t in view["tools"]}
    assert rows["read_file"]["calls"] == 3 and rows["read_file"]["failed"] == 0
    assert rows["read_file"]["median_ms"] == 50 and rows["read_file"]["p95_ms"] == 60
    web = rows["web_lookup"]
    assert web["calls"] == 2 and web["failed"] == 1 and web["rate"] == 0.5
    assert web["last_error"]["outcome"] == "error:TimeoutError"
    assert web["used_by"] == ["turn"]
    per_day = {d["day"]: d for d in view["tool"]["per_day"]}
    assert per_day["2026-11-08"] == {"day": "2026-11-08", "gap": False, "ok": 0, "failed": 1}
    assert view["tool"]["slowest"][0]["ms"] == 15000
    assert all("result" not in s for s in view["tool"]["slowest"])  # the result text never reaches the panel


def test_today_is_live_and_not_closed(world):
    rollups, inst = world
    rollups.run()
    view = us.usage_view(rollups, [inst], range_name="7d")
    today = next(d for d in view["per_day"] if d["day"] == "2026-11-10")
    assert not today["gap"] and today["total"] > 0
    assert "2026-11-10" not in rollups.usage_path("prod").read_text()


def test_the_csv(world):
    rollups, inst = world
    rollups.run()
    text = us.usage_csv(rollups, [inst], range_name="7d")
    lines = text.strip().splitlines()
    assert lines[0].split(",") == list(us.CSV_COLUMNS)
    assert any(",elevenlabs,tts,eleven_v4_turbo,tts,turn,window,1," in line and "chars=2000" in line for line in lines)


def test_an_unknown_range_is_refused():
    with pytest.raises(ValueError):
        us.range_days("week", NOW.date())


def test_model_provider():
    assert [us.model_provider(m) for m in ("claude-opus-4-8", "gemini-3.1-pro-preview", "gpt-5.5", "deepseek-chat",
                                            "", "local-embed")] == ["anthropic", "google", "openai", "deepseek",
                                                                    "other", "other"]


def test_a_broken_ledger_never_stops_the_rollup(world):
    rollups, inst = world
    (inst.data / "cache-log.jsonl").write_text("{not json\n" + json.dumps(_call(7)) + "\n", encoding="utf-8")
    assert rollups.run() == {"prod": 3}

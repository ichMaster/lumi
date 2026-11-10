"""v2.10 LUMI-253 — the admin readers: read-only views of an instance folder and of the box.

Fixture instance folders under ``tmp_path`` (settings.yaml, secrets.yaml, data/ with ledgers and logs), an injected
"now"; no network, no paid call. Every reader runs over a root that is fingerprinted unchanged afterwards.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from admin import readers as rd

TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 11, 10, 18, 0, tzinfo=TZ)
TOKEN = "tok-SECRET-ADMIN-READERS-1"
GEMINI = "g-SECRET-ADMIN-READERS-2"


def _call(ts: datetime, kind: str, model: str = "gemini-2.5-flash", *, inp=1000, out=100, rd_=0, wr=0,
          latency=1000, pre=None, post=None) -> dict:
    return {"ts": ts.isoformat(), "kind": kind, "model": model, "input": inp, "output": out, "cache_read": rd_,
            "cache_write": wr, "latency_ms": latency, "pre_ms": pre, "post_ms": post, "session_id": "s1"}


@pytest.fixture
def inst(tmp_path) -> rd.Instance:
    root = tmp_path / "dev"
    data = root / "data"
    data.mkdir(parents=True)
    (root / "settings.yaml").write_text("instance:\n  env: dev\nscheduler:\n  enabled: true\nbridge:\n"
                                        "  enabled: true\nmodel:\n  prompt_cache_ttl: 5m\n", encoding="utf-8")
    (root / "secrets.yaml").write_text(f"server:\n  token: {TOKEN}\nproviders:\n  gemini_api_key: {GEMINI}\n",
                                       encoding="utf-8")
    (root / "secrets.yaml").chmod(0o600)
    calls = [
        _call(NOW - timedelta(days=9), "reply"),                                    # outside 7 days
        _call(NOW - timedelta(days=1), "reply", latency=2000, pre=100, post=50),
        _call(NOW - timedelta(hours=3), "think", inp=2000, out=200, rd_=1000),
        _call(NOW - timedelta(hours=2), "reply", "claude-opus-4-8", inp=1_000_000, out=0, latency=9000,
              pre=300, post=40),
        _call(NOW - timedelta(minutes=10), "reply", latency=4000, pre=200, post=60),
    ]
    (data / "cache-log.jsonl").write_text("\n".join(json.dumps(c) for c in calls) + "\nnot json\n",
                                          encoding="utf-8")
    ledger = [{"session_id": "s1", "ended_at": (NOW - timedelta(hours=1)).isoformat()},
              {"session_id": "s0", "ended_at": (NOW - timedelta(days=1)).isoformat()}]
    (data / "usage-ledger.jsonl").write_text("\n".join(json.dumps(r) for r in ledger) + "\n", encoding="utf-8")
    log = [
        "2026-11-09 10:00:00,001 ERROR lumi.tui: an old error",                    # > 24 h
        "2026-11-10 09:00:00,001 ERROR lumi.brain: inbox turn failed",
        "Traceback (most recent call last):",
        f"  a line with the key {GEMINI} in it",
        "2026-11-10 17:30:00,001 WARNING lumi.llm: Gemini returned no answer — asking once more",
        "2026-11-10 17:40:00,001 INFO lumi.server: turn done",
        f"2026-11-10 17:50:00,001 INFO lumi.server: token {TOKEN} must never show",
    ]
    (data / "lumi.log").write_text("\n".join(log) + "\n", encoding="utf-8")
    (data / "telegram-inbound.log").write_text("2026-11-10 17:00:00 INFO lumi.telegram.inbound: inbound up\n",
                                               encoding="utf-8")
    (data / "mood.log").write_text(
        "\n\n===== 2026-11-09 =====\nвчора\nРЕЗОЛЮЦІЯ: спокій\n"
        "\n\n===== 2026-11-10 =====\nсьогодні\nРЕЗОЛЮЦІЯ: тиша і тепло\nТЕМА: quiet-collapse\n", encoding="utf-8")
    (data / "schedule.state").write_text(json.dumps({"a": (NOW - timedelta(hours=5)).isoformat(),
                                                     "b": (NOW - timedelta(minutes=20)).isoformat()}),
                                         encoding="utf-8")
    (data / "inbox.jsonl").write_text("".join(json.dumps({"id": i}) + "\n" for i in range(1, 6)), encoding="utf-8")
    (data / "inbox.pos").write_text("5", encoding="utf-8")
    (data / "outbox.jsonl").write_text("".join(json.dumps({"id": i}) + "\n" for i in range(1, 9)), encoding="utf-8")
    (data / "outbox.sent").write_text("6", encoding="utf-8")
    (data / "env-marker").write_text("dev\n", encoding="utf-8")
    return rd.Instance("dev", root, "http://127.0.0.1:8766", "lumi-dev")


def _fingerprint(root: Path) -> dict[str, tuple[str, float]]:
    return {str(p.relative_to(root)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime)
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_usage_aggregates(inst):
    u = rd.usage(inst, now=NOW)
    assert u["total"]["calls"] == 4  # the 9-day-old row and the malformed line are out
    assert u["per_kind"]["reply"]["calls"] == 3 and u["per_kind"]["think"]["calls"] == 1
    assert set(u["per_day"]) == {"2026-11-09", "2026-11-10"}
    # cost: opus 1M input = $5; flash 1000 in + 100 out = 0.0003 + 0.00025; think 2000 in, 200 out, 1000 cache read
    assert u["per_model"]["claude-opus-4-8"]["cost"] == pytest.approx(5.0)
    assert u["per_model"]["gemini-2.5-flash"]["cost"] == pytest.approx(
        2 * (1000 * 0.30 + 100 * 2.50) / 1e6 + (2000 * 0.30 + 200 * 2.50 + 1000 * 0.03) / 1e6)
    # cache read / (input + cache read + cache write): 1000 / (1000 + 2000 + 1_000_000 + 1000 + 1000), 4 places
    assert u["cache_hit_ratio"] == round(1000 / 1_005_000, 4)
    assert u["latency"]["model_median_ms"] == 4000 and u["latency"]["model_p95_ms"] == 9000
    assert u["latency"]["turn_median_ms"] == 4260  # 200 + 4000 + 60
    assert u["latency"]["pre_median_ms"] == 200 and u["latency"]["post_median_ms"] == 50
    assert [s["latency_ms"] for s in u["slowest"]] == [9000, 4000, 2000]
    assert u["turns_today"] == 2 and u["last_turn_age_s"] == 600 and u["last_call_age_s"] == 600
    assert u["sessions_per_day"] == {"2026-11-09": 1, "2026-11-10": 1}


def test_errors_and_model_failures(inst):
    e = rd.errors(inst, now=NOW)
    assert e["errors_24h"] == 1 and e["model_failures_1h"] == 1
    assert e["last_error"].endswith("lumi.brain: inbox turn failed")


def test_a_log_tail_filters_and_redacts_every_secret(inst):
    lines = rd.log_tail(inst, "lumi")
    text = json.dumps(lines, ensure_ascii=False)
    assert TOKEN not in text and GEMINI not in text and rd.REDACTED in text
    assert [r["level"] for r in rd.log_tail(inst, "lumi", level="warning")] == ["ERROR", "ERROR", "WARNING"]
    assert [r["source"] for r in rd.log_tail(inst, "lumi", q="no answer")] == ["lumi.llm"]
    assert rd.log_tail(inst, "telegram-inbound")[0]["message"] == "inbound up"
    assert rd.log_tail(inst, "../../etc/passwd") == []  # only the known sources


def test_mood_scheduler_bus_marker_size(inst):
    assert rd.mood_today(inst, today="2026-11-10") == {"present": True, "complete": True, "theme": "quiet-collapse"}
    assert rd.mood_today(inst, today="2026-11-11")["present"] is False
    (inst.data / "mood.log").write_text("\n\n===== 2026-11-10 =====\nсьогодні, обірвано на півсл", encoding="utf-8")
    assert rd.mood_today(inst, today="2026-11-10") == {"present": True, "complete": False, "theme": None}
    s = rd.scheduler(inst, now=NOW)
    assert s["enabled"] is True and s["age_s"] == 1200 and s["rows"] == 2
    assert rd.bus(inst) == {"inbox_pending": 0, "outbox_pending": 2, "bridge": True}
    assert rd.marker(inst) == "dev"
    rd._SIZE_CACHE.clear()
    assert rd.data_size(inst) == sum(p.stat().st_size for p in inst.data.rglob("*") if p.is_file())


def test_the_server_token_is_read_but_never_in_a_view(inst):
    assert rd.server_token(inst) == TOKEN
    views = [rd.usage(inst, now=NOW), rd.errors(inst, now=NOW), rd.log_tail(inst), rd.scheduler(inst, now=NOW),
             rd.bus(inst), rd.mood_today(inst, today="2026-11-10")]
    blob = json.dumps(views, ensure_ascii=False, default=str)
    assert TOKEN not in blob and GEMINI not in blob


def test_every_reader_leaves_the_folder_untouched(inst):
    before = _fingerprint(inst.root)
    rd.usage(inst, now=NOW), rd.errors(inst, now=NOW), rd.log_tail(inst, "lumi", level="info", q="x")
    rd.mood_today(inst, today="2026-11-10"), rd.scheduler(inst, now=NOW), rd.bus(inst), rd.marker(inst)
    rd.server_token(inst), rd.instance_settings(inst)
    rd._SIZE_CACHE.clear()
    rd.data_size(inst)
    assert _fingerprint(inst.root) == before


def test_missing_sources_never_raise(tmp_path):
    empty = rd.Instance("prod", tmp_path / "nothing", "http://127.0.0.1:8765", "lumi-prod")
    assert rd.usage(empty, now=NOW)["total"]["calls"] == 0
    assert rd.errors(empty, now=NOW) == {"errors_24h": 0, "model_failures_1h": 0, "last_error": None}
    assert rd.log_tail(empty) == [] and rd.mood_today(empty, today="2026-11-10")["present"] is False
    assert rd.scheduler(empty, now=NOW)["newest"] is None and rd.marker(empty) is None
    assert rd.bus(empty)["inbox_pending"] is None and rd.data_size(empty) is None
    assert rd.server_token(empty) == ""


def test_a_large_file_is_read_from_its_end_only(tmp_path):
    big = tmp_path / "big.jsonl"
    big.write_text("".join(json.dumps({"id": i, "pad": "x" * 100}) + "\n" for i in range(50_000)), encoding="utf-8")
    rows = rd.jsonl_tail(big, limit=100_000)
    assert 0 < len(rows) < 1_000 and rows[-1]["id"] == 49_999  # the tail, starting on a whole line
    assert rd.pending(big, tmp_path / "missing.pos", scan_bytes=10_000) > 0


def test_the_box(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "uptime").write_text("12345.67 999.0\n")
    (proc / "loadavg").write_text("0.50 0.40 0.30 1/200 12345\n")
    (proc / "meminfo").write_text("MemTotal:       16000000 kB\nMemFree: 1 kB\nMemAvailable:    8000000 kB\n")
    b = rd.box([tmp_path], proc=proc)
    assert b["uptime_s"] == 12345 and b["load"] == [0.5, 0.4, 0.3]
    assert b["memory"] == {"total": 16000000 * 1024, "available": 8000000 * 1024}
    assert str(tmp_path) in b["disks"] and b["disks"][str(tmp_path)]["free"] > 0
    assert rd.box([], proc=tmp_path / "none") == {"uptime_s": None, "load": None, "memory": None, "disks": {}}

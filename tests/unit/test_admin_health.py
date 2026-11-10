"""v2.10 LUMI-254 — the health checks (pure, table-driven) and the every-minute runner.

A fake HTTP client records every URL (only /v1/health and /v1/state may appear — no paid call); fixture instance
folders; an injected clock. No network.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from admin import health as h
from admin import readers as rd

TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 11, 10, 15, 0, tzinfo=TZ)


def _f(**kw) -> h.Facts:
    base = dict(instance="dev", health={"ok": True, "env": "dev", "version": "2.10.0"}, health_ms=40.0,
                state_status=200, mood={"present": True, "complete": True, "theme": "calm"}, hour=15,
                last_call_age_s=300, model_failures_1h=0,
                services={"server": "running", "telegram-inbound": "running", "telegram-outbound": "running"},
                bus={"bridge": True, "inbox_pending": 0, "outbox_pending": 1},
                scheduler={"enabled": True, "age_s": 600}, disk_free=0.5, marker="dev")
    base.update(kw)
    return h.Facts(**base)


def _status(f: h.Facts, name: str) -> str:
    return next(r.status for r in h.evaluate(f) if r.name == name)


def test_all_green():
    assert {r.name: r.status for r in h.evaluate(_f())} == {c: h.GREEN for c in h.CHECKS}


@pytest.mark.parametrize("name, facts, want", [
    ("server", dict(health=None, health_error="unreachable"), h.RED),
    ("server", dict(health={"env": "prod", "version": "x"}), h.RED),
    ("server", dict(health_ms=2500.0), h.AMBER),
    ("state", dict(state_status=None), h.AMBER),
    ("state", dict(state_status=401), h.RED),
    ("state", dict(state_status=0), h.RED),
    ("mood", dict(mood={"present": True, "complete": False}), h.RED),
    ("mood", dict(mood={"present": False}, hour=9), h.AMBER),
    ("mood", dict(mood={"present": False}, hour=13), h.RED),
    ("model", dict(model_failures_1h=3), h.RED),
    ("model", dict(model_failures_1h=1), h.AMBER),
    ("model", dict(last_call_age_s=None), h.AMBER),
    ("model", dict(last_call_age_s=13 * 3600), h.AMBER),
    ("model", dict(services={"server": "stopped"}), h.OFF),
    ("telegram", dict(bus={"bridge": False}), h.OFF),
    ("telegram", dict(services={"server": "running", "telegram-inbound": "running",
                                "telegram-outbound": "stopped"}), h.RED),
    ("telegram", dict(bus={"bridge": True, "inbox_pending": 5, "outbox_pending": 0}), h.AMBER),
    ("telegram", dict(bus={"bridge": True, "inbox_pending": 0, "outbox_pending": 50}), h.RED),
    ("scheduler", dict(scheduler={"enabled": False}), h.OFF),
    ("scheduler", dict(scheduler={"enabled": True, "age_s": None}), h.AMBER),
    ("scheduler", dict(scheduler={"enabled": True, "age_s": 7 * 3600}), h.AMBER),
    ("scheduler", dict(scheduler={"enabled": True, "age_s": 25 * 3600}), h.RED),
    ("disk", dict(disk_free=0.08), h.AMBER),
    ("disk", dict(disk_free=0.03), h.RED),
    ("disk", dict(disk_free=None), h.AMBER),
    ("marker", dict(marker="prod"), h.RED),
    ("marker", dict(marker=None), h.AMBER),
])
def test_each_check(name, facts, want):
    assert _status(_f(**facts), name) == want


# --- the runner over fixture instances -----------------------------------------------------------------------


@pytest.fixture
def instances(tmp_path):
    out = []
    for name, port in (("prod", 8765), ("dev", 8766)):
        root = tmp_path / name
        (root / "data").mkdir(parents=True)
        (root / "secrets.yaml").write_text(f"server:\n  token: tok-{name}\n", encoding="utf-8")
        (root / "secrets.yaml").chmod(0o600)
        (root / "data" / "env-marker").write_text(f"{name}\n", encoding="utf-8")
        out.append(rd.Instance(name, root, f"http://192.168.1.197:{port}", f"lumi-{name}"))
    return out


class FakeHttp:
    def __init__(self):
        self.down: set[str] = set()
        self.urls: list[str] = []

    def __call__(self, url, headers):
        self.urls.append(url)
        port = url.split(":")[2].split("/")[0]
        env = {"8765": "prod", "8766": "dev"}[port]
        if env in self.down:
            return 0, None, 5000.0
        if url.endswith("/v1/state"):
            ok = headers.get("Authorization") == f"Bearer tok-{env}"
            return (200, {"model": "x"}, 30.0) if ok else (401, None, 30.0)
        return 200, {"ok": True, "env": env, "version": "2.10.0"}, 20.0


def test_a_stopped_server_turns_red_on_the_next_run_and_green_after(instances, tmp_path):
    http = FakeHttp()
    clock = iter(NOW + timedelta(minutes=i) for i in range(10))
    runner = h.HealthRunner(instances, tmp_path / "admin" / "health.jsonl", http=http, clock=lambda: next(clock))
    first = runner.run_once()
    assert {r.name: r.status for r in first["dev"]}["server"] == h.GREEN
    http.down.add("dev")
    second = runner.run_once()
    dev = {r.name: r.status for r in second["dev"]}
    assert dev["server"] == h.RED and dev["state"] == h.RED
    assert {r.name: r.status for r in second["prod"]}["server"] == h.GREEN  # the other instance is unaffected
    http.down.clear()
    assert {r.name: r.status for r in runner.run_once()["dev"]}["server"] == h.GREEN
    rows = [json.loads(line) for line in (tmp_path / "admin" / "health.jsonl").read_text().splitlines()]
    assert len(rows) == 6 and rows[3]["instance"] == "dev" and rows[3]["checks"]["server"] == h.RED
    history = runner.snapshot()["instances"]["dev"][0]["history"]
    assert [s for _, s in history] == [h.GREEN, h.RED, h.GREEN]


def test_the_checks_make_no_paid_call(instances, tmp_path):
    http = FakeHttp()
    h.HealthRunner(instances, tmp_path / "h.jsonl", http=http, clock=lambda: NOW).run_once()
    assert http.urls and all(u.endswith(("/v1/health", "/v1/state")) for u in http.urls)


def test_the_history_rotates_at_its_cap(instances, tmp_path):
    path = tmp_path / "h.jsonl"
    runner = h.HealthRunner(instances, path, http=FakeHttp(), clock=lambda: NOW, max_bytes=500)
    for _ in range(5):
        runner.run_once()
    assert path.with_name("h.jsonl.1").exists() and path.stat().st_size < 2000


def test_one_instance_failing_to_gather_never_stops_the_others(instances, tmp_path, monkeypatch):
    def boom(inst, now, **kw):
        if inst.name == "dev":
            raise RuntimeError("surprise")
        return h.Facts(instance=inst.name, health={"env": inst.name}, health_ms=1.0)

    monkeypatch.setattr(h, "gather", boom)
    out = h.HealthRunner(instances, tmp_path / "h.jsonl", http=FakeHttp(), clock=lambda: NOW).run_once()
    assert out["dev"][0].status == h.RED and "RuntimeError" in out["dev"][0].detail
    assert {r.name: r.status for r in out["prod"]}["server"] == h.GREEN


def test_the_server_token_never_reaches_the_snapshot_or_history(instances, tmp_path):
    runner = h.HealthRunner(instances, tmp_path / "h.jsonl", http=FakeHttp(), clock=lambda: NOW)
    runner.run_once()
    blob = json.dumps(runner.snapshot()) + (tmp_path / "h.jsonl").read_text()
    assert "tok-prod" not in blob and "tok-dev" not in blob

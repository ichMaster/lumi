"""A shared fixture world for the admin panel's API tests (v2.10): two instance folders, a fake container runner,
a fake HTTP client for their servers, the admin config. No network, no Docker, no paid call."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from admin import containers as ct
from admin import health as hl
from admin import readers as rd
from admin.app import create_app
from admin.registry import AdminConfig

TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 11, 10, 15, 0, tzinfo=TZ)
ADMIN_TOKEN = "admin-" + "A" * 40
SECRETS = {
    "prod": {"server.token": "tok-PROD-SECRET-0001", "providers.gemini_api_key": "g-PROD-SECRET-0002",
             "telegram.token": "999:PROD-SECRET-0003"},
    "dev": {"server.token": "tok-DEV-SECRET-0004", "providers.anthropic_api_key": "sk-DEV-SECRET-0005"},
}


def all_secret_values() -> list[str]:
    return [v for s in SECRETS.values() for v in s.values()] + [ADMIN_TOKEN]


def build_instance(root: Path, name: str) -> rd.Instance:
    data = root / "data"
    data.mkdir(parents=True)
    groups: dict[str, dict] = {}
    for key, value in SECRETS[name].items():
        g, k = key.split(".")
        groups.setdefault(g, {})[k] = value
    (root / "secrets.yaml").write_text("".join(f"{g}:\n" + "".join(f"  {k}: '{v}'\n" for k, v in b.items())
                                               for g, b in groups.items()), encoding="utf-8")
    (root / "secrets.yaml").chmod(0o600)
    (root / "settings.yaml").write_text(f"instance:\n  env: {name}\nbridge:\n  enabled: true\nscheduler:\n"
                                        "  enabled: true\n", encoding="utf-8")
    calls = [{"ts": (NOW - timedelta(minutes=m)).isoformat(), "kind": k, "model": "gemini-2.5-flash", "input": 1000,
              "output": 100, "cache_read": 500, "cache_write": 0, "latency_ms": 3000 + m, "pre_ms": 200,
              "post_ms": 10, "session_id": "s1"} for m, k in ((30, "reply"), (20, "think"), (5, "reply"))]
    (data / "cache-log.jsonl").write_text("\n".join(json.dumps(c) for c in calls) + "\n", encoding="utf-8")
    secret = SECRETS[name]["server.token"]
    (data / "lumi.log").write_text(
        "2026-11-10 14:00:00,001 ERROR lumi.brain: inbox turn failed\n"
        f"2026-11-10 14:10:00,001 INFO lumi.server: a careless line with {secret} in it\n", encoding="utf-8")
    (data / "telegram-outbound.log").write_text("2026-11-10 14:00:00 INFO lumi.telegram.outbound: outbound up\n",
                                                encoding="utf-8")
    (data / "mood.log").write_text(f"\n\n===== {NOW.date().isoformat()} =====\nРЕЗОЛЮЦІЯ: тиша\nТЕМА: calm\n",
                                   encoding="utf-8")
    (data / "schedule.state").write_text(json.dumps({"x": (NOW - timedelta(minutes=10)).isoformat()}),
                                         encoding="utf-8")
    (data / "env-marker").write_text(f"{name}\n", encoding="utf-8")
    port = {"prod": 8765, "dev": 8766}[name]
    return rd.Instance(name, root, f"http://192.168.1.197:{port}", f"lumi-{name}")


class FakeServers:
    """The two instances' servers: /v1/health open, /v1/state behind each one's token; ``down`` takes one away."""

    def __init__(self) -> None:
        self.down: set[str] = set()
        self.urls: list[str] = []

    def __call__(self, url: str, headers: dict):
        self.urls.append(url)
        name = {"8765": "prod", "8766": "dev"}[url.split(":")[2].split("/")[0]]
        if name in self.down:
            return 0, None, 5000.0
        if url.endswith("/v1/state"):
            if headers.get("Authorization") != f"Bearer {SECRETS[name]['server.token']}":
                return 401, None, 10.0
            return 200, {"model": "gemini-3.1-pro-preview", "provider": "gemini", "profile": "gemini",
                         "mood": "тиша", "theme": "calm", "session_id": "s1", "totals": {"turns": 4},
                         "extra_field_never_forwarded": "x"}, 10.0
        return 200, {"ok": True, "env": name, "version": "2.10.0"}, 15.0


def build_world(tmp_path: Path):
    instances = tuple(build_instance(tmp_path / n, n) for n in ("prod", "dev"))
    cfg = AdminConfig(home=tmp_path / "admin", token=ADMIN_TOKEN, instances=instances, host="127.0.0.1", port=8767)
    runner = ct.FakeRunner(["prod", "dev"])
    runner.log_lines[("dev", "server")] = ["serving on 0.0.0.0:8766", f"oops {SECRETS['dev']['server.token']}"]
    servers = FakeServers()
    health = hl.HealthRunner(list(instances), cfg.home / "health.jsonl", http=servers,
                             services=lambda inst: runner.list(inst.name), clock=lambda: NOW)
    app = create_app(cfg, runner, health, http=servers, clock=lambda: NOW, background=False)
    return app, cfg, runner, servers, health


AUTH = {"Authorization": f"Bearer {ADMIN_TOKEN}"}

"""v2.10 LUMI-256 — the admin API over two fixture instances, a fake container runner and fake servers."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests._admin_world import AUTH, build_world


@pytest.fixture
def world(tmp_path):
    app, cfg, runner, servers, health = build_world(tmp_path)
    return TestClient(app), runner, servers, health


def test_the_overview_shows_prod_and_dev_side_by_side(world):
    client, *_ = world
    data = client.get("/api/overview", headers=AUTH).json()
    by = {i["name"]: i for i in data["instances"]}
    assert set(by) == {"prod", "dev"}
    dev = by["dev"]
    assert dev["up"] is True and dev["version"] == "2.10.0"
    assert dev["state"]["model"] == "gemini-3.1-pro-preview" and dev["state"]["session_turns"] == 4
    assert "extra_field_never_forwarded" not in dev["state"]  # only the fields the panel shows
    assert dev["mood_today"] == {"present": True, "complete": True, "theme": "calm"}
    assert dev["turns_today"] == 2 and dev["today"]["calls"] == 3 and dev["today"]["cost"] > 0
    assert dev["latency"]["turn_median_ms"] is not None and dev["errors"]["errors_24h"] == 1
    assert dev["services"] == {"server": "running", "telegram-inbound": "running", "telegram-outbound": "running"}
    assert "box" in data


def test_health_turns_red_when_a_server_stops_and_green_after(world):
    client, runner, servers, health = world
    health.run_once()
    servers.down.add("dev")
    runner.act("dev", "server", "stop")
    health.run_once()
    dev = {c["name"]: c for c in client.get("/api/health", headers=AUTH).json()["instances"]["dev"]}
    assert dev["server"]["status"] == "red" and dev["state"]["status"] == "red"
    servers.down.clear()
    runner.act("dev", "server", "start")
    health.run_once()
    dev = {c["name"]: c for c in client.get("/api/health", headers=AUTH).json()["instances"]["dev"]}
    assert dev["server"]["status"] == "green"
    assert [s for _, s in dev["server"]["history"]] == ["green", "red", "green"]


def test_a_stopped_dev_container_is_seen_and_started(world):
    client, runner, *_ = world
    runner.act("dev", "telegram-inbound", "stop")
    assert client.get("/api/services", headers=AUTH).json()["dev"]["telegram-inbound"]["state"] == "stopped"
    r = client.post("/api/services/dev/telegram-inbound/start", headers=AUTH, json={})
    assert r.status_code == 200 and r.json()["result"] == "done"
    assert client.get("/api/services", headers=AUTH).json()["dev"]["telegram-inbound"]["state"] == "running"


def test_a_prod_restart_needs_the_typed_confirmation(world):
    client, runner, *_ = world
    r = client.post("/api/services/prod/server/restart", headers=AUTH, json={})
    assert r.status_code == 409 and "type 'prod'" in r.json()["detail"]
    assert client.post("/api/services/prod/server/restart", headers=AUTH, json={"confirm": "yes"}).status_code == 409
    assert ("prod", "server", "restart") not in runner.calls
    r = client.post("/api/services/prod/server/restart", headers=AUTH, json={"confirm": "prod"})
    assert r.status_code == 200 and ("prod", "server", "restart") in runner.calls


@pytest.mark.parametrize("path, status", [
    ("/api/services/staging/server/restart", 404),
    ("/api/services/dev/store/restart", 409),
    ("/api/services/dev/server/kill", 409),
])
def test_unknown_targets_are_refused(world, path, status):
    client, runner, *_ = world
    assert client.post(path, headers=AUTH, json={}).status_code == status
    assert runner.calls == []


def test_usage_and_logs(world):
    client, *_ = world
    u = client.get("/api/usage?instance=prod&days=7", headers=AUTH).json()
    assert u["total"]["calls"] == 3 and u["per_kind"]["reply"]["calls"] == 2
    assert client.get("/api/usage?instance=nope", headers=AUTH).status_code == 404
    lines = client.get("/api/logs?instance=dev&source=lumi&level=ERROR", headers=AUTH).json()["lines"]
    assert [x["message"] for x in lines] == ["inbox turn failed"]
    out = client.get("/api/logs?instance=prod&source=telegram-outbound", headers=AUTH).json()["lines"]
    assert out[0]["message"] == "outbound up"
    cont = client.get("/api/logs?instance=dev&source=container:server", headers=AUTH).json()["lines"]
    assert cont[0]["message"] == "serving on 0.0.0.0:8766" and "•••" in cont[1]["message"]
    assert client.get("/api/logs?instance=dev&source=../x", headers=AUTH).status_code == 404
    assert client.get("/api/logs?instance=dev&source=container:db", headers=AUTH).status_code == 404


def test_the_page_and_healthz_are_open(world):
    client, *_ = world
    assert client.get("/healthz").json() == {"ok": True}
    page = client.get("/")
    assert page.status_code == 200 and "Lumi admin" in page.text
    assert client.get("/static/app.js").status_code == 200

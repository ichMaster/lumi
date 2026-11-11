"""v2.10 LUMI-256 — the admin API over two fixture instances, a fake container runner and fake servers; v2.11
LUMI-263 — configure: settings, secrets, history, sections, promote, the audit, the deploy-agent lock."""

from __future__ import annotations

import fcntl
import http.client

import pytest
from fastapi.testclient import TestClient

from admin import containers as ct
from tests._admin_world import AUTH, SECRETS, build_world


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


# --- v2.11 LUMI-263: configure ---------------------------------------------------------------------------------------

NEW_TOKEN = "tok-DEV-ROTATED-SECRET-9876"


def _audit(client, **params):
    return client.get("/api/audit", headers=AUTH, params=params).json()["entries"]


def _file(tmp_path, instance, name="settings.yaml"):
    return (tmp_path / instance / name).read_text(encoding="utf-8")


@pytest.fixture
def conf(tmp_path):
    app, cfg, runner, servers, health = build_world(tmp_path)
    return TestClient(app), runner, cfg, tmp_path


def test_a_dev_setting_is_previewed_applied_with_its_restart_and_rolled_back(conf):
    client, runner, cfg, tmp_path = conf
    view = client.get("/api/settings/dev", headers=AUTH).json()
    assert view["deploy_busy"] is False and view["instance"] == "dev"
    prev = client.post("/api/settings/dev/preview", headers=AUTH, json={"changes": {"persona.think_show": "open"}})
    pv = prev.json()
    assert pv["ok"] and pv["changes"] == {"persona.think_show": [None, "open"]} and "+  think_show: open" in pv["diff"]
    assert "think_show" not in _file(tmp_path, "dev")  # a preview writes nothing
    r = client.post("/api/settings/dev/apply", headers=AUTH,
                    json={"changes": {"persona.think_show": "open"}, "hashes": pv["hashes"]})
    assert r.status_code == 200, r.text
    assert r.json()["restarted"] == {s: "restarted" for s in ("server", "telegram-inbound", "telegram-outbound")}
    assert [c for c in runner.calls if c[0] == "dev"] == [("dev", s, "restart")
                                                           for s in ("server", "telegram-inbound", "telegram-outbound")]
    assert "think_show: open" in _file(tmp_path, "dev")
    [entry] = client.get("/api/history/dev", headers=AUTH).json()["entries"]
    back = client.post(f"/api/history/dev/{entry['ts']}/rollback", headers=AUTH, json={})
    assert back.status_code == 200 and "think_show" not in _file(tmp_path, "dev")
    assert [(a["action"], a["result"]) for a in _audit(client)] == [("rollback", "done"), ("settings", "done")]


def test_review_1_a_rollback_is_previewed_and_a_stale_one_refused(conf):
    client, runner, cfg, tmp_path = conf
    for change in ({"memory.window": "30"}, {"persona.think_show": "open"}):
        pv = client.post("/api/settings/dev/preview", headers=AUTH, json={"changes": change}).json()
        assert client.post("/api/settings/dev/apply", headers=AUTH,
                           json={"changes": change, "hashes": pv["hashes"]}).status_code == 200
    newer, older = client.get("/api/history/dev", headers=AUTH).json()["entries"]
    pv = client.post(f"/api/history/dev/{older['ts']}/preview", headers=AUTH).json()
    assert pv["ok"] and pv["changes"] == {"memory.window": [30, None], "persona.think_show": ["open", None]}
    assert newer["ts"] in pv["notes"][0] and "-  think_show: open" in pv["diff"]
    assert client.post("/api/history/dev/20990101-000000Z/preview", headers=AUTH).status_code == 404
    before = _file(tmp_path, "dev")
    pv2 = client.post("/api/settings/dev/preview", headers=AUTH, json={"changes": {"memory.window": "31"}}).json()
    client.post("/api/settings/dev/apply", headers=AUTH, json={"changes": {"memory.window": "31"},
                                                               "hashes": pv2["hashes"]})  # a change in between
    after = _file(tmp_path, "dev")
    r = client.post(f"/api/history/dev/{older['ts']}/rollback", headers=AUTH, json={"hashes": pv["hashes"]})
    assert r.status_code == 409 and "changed since this preview" in r.json()["detail"]
    assert _file(tmp_path, "dev") == after != before
    fresh = client.post(f"/api/history/dev/{older['ts']}/preview", headers=AUTH).json()
    r = client.post(f"/api/history/dev/{older['ts']}/rollback", headers=AUTH, json={"hashes": fresh["hashes"]})
    assert r.status_code == 200 and "window" not in _file(tmp_path, "dev") and "think_show" not in _file(tmp_path, "dev")


def test_a_stale_preview_and_an_invalid_value_are_refused_and_audited(conf):
    client, runner, cfg, tmp_path = conf
    before = _file(tmp_path, "dev")
    r = client.post("/api/settings/dev/apply", headers=AUTH, json={"changes": {"memory.window": "30"}, "hashes": {}})
    assert r.status_code == 409 and "changed since this preview" in r.json()["detail"]
    pv = client.post("/api/settings/dev/preview", headers=AUTH, json={"changes": {"memory.window": "0"}}).json()
    assert not pv["ok"] and "memory.window" in pv["problems"][0]
    r = client.post("/api/settings/dev/apply", headers=AUTH, json={"changes": {"memory.window": "0"},
                                                                  "hashes": pv["hashes"]})
    assert r.status_code == 409 and _file(tmp_path, "dev") == before and runner.calls == []
    assert [a["result"].split(":")[0] for a in _audit(client)] == ["refused", "refused"]


def test_prod_needs_the_confirmation_then_applies(conf):
    client, runner, cfg, tmp_path = conf
    before = _file(tmp_path, "prod")
    pv = client.post("/api/settings/prod/preview", headers=AUTH, json={"changes": {"memory.window": "30"}}).json()
    body = {"changes": {"memory.window": "30"}, "hashes": pv["hashes"]}
    for confirm in (None, "yes"):
        r = client.post("/api/settings/prod/apply", headers=AUTH, json=body | {"confirm": confirm})
        assert r.status_code == 409 and "type 'prod'" in r.json()["detail"]
    assert _file(tmp_path, "prod") == before and runner.calls == []
    r = client.post("/api/settings/prod/apply", headers=AUTH, json=body | {"confirm": "prod"})
    assert r.status_code == 200 and "window: 30" in _file(tmp_path, "prod")
    assert ("prod", "server", "restart") in runner.calls
    assert _audit(client, instance="prod")[0]["changes"] == {"memory.window": [None, 30]}


def test_a_stopped_service_is_not_started_by_an_apply(conf):
    client, runner, cfg, tmp_path = conf
    runner.act("dev", "telegram-inbound", "stop")
    pv = client.post("/api/settings/dev/preview", headers=AUTH, json={"changes": {"memory.window": "30"}}).json()
    r = client.post("/api/settings/dev/apply", headers=AUTH, json={"changes": {"memory.window": "30"},
                                                                  "hashes": pv["hashes"]})
    assert r.json()["restarted"]["telegram-inbound"].startswith("not running")
    assert runner.state[("dev", "telegram-inbound")] == "stopped"


def test_review_2_a_docker_error_after_the_write_is_reported_and_audited(conf, monkeypatch):
    client, runner, cfg, tmp_path = conf
    real_act = runner.act

    def flaky(instance, service, action, confirm=None):
        if service == "telegram-inbound":
            raise http.client.BadStatusLine("")  # not an OSError: it escaped before, after the files were written
        return real_act(instance, service, action, confirm)

    monkeypatch.setattr(runner, "act", flaky)
    pv = client.post("/api/settings/dev/preview", headers=AUTH, json={"changes": {"memory.window": "30"}}).json()
    r = client.post("/api/settings/dev/apply", headers=AUTH, json={"changes": {"memory.window": "30"},
                                                                  "hashes": pv["hashes"]})
    assert r.status_code == 200 and "window: 30" in _file(tmp_path, "dev")
    assert r.json()["restarted"]["telegram-inbound"].startswith("failed")
    assert r.json()["restarted"]["server"] == "restarted"
    [line] = _audit(client)
    assert line["result"].startswith("done; restart") and "telegram-inbound" in line["result"]
    assert line["changes"] == {"memory.window": [None, 30]}
    r = client.post("/api/services/dev/telegram-inbound/restart", headers=AUTH, json={})
    assert r.status_code == 502 and _audit(client)[0]["result"].startswith("failed: docker")

    monkeypatch.setattr(runner, "list", lambda instance: {s: {"state": "unknown"} for s in ct.SERVICES})
    pv = client.post("/api/settings/dev/preview", headers=AUTH, json={"changes": {"memory.window": "31"}}).json()
    r = client.post("/api/settings/dev/apply", headers=AUTH, json={"changes": {"memory.window": "31"},
                                                                  "hashes": pv["hashes"]})
    assert all(x.startswith("failed: Docker can't say") for x in r.json()["restarted"].values())
    assert _audit(client)[0]["result"].startswith("done; restart")


def test_a_secret_is_rotated_and_never_echoed(conf):
    client, runner, cfg, tmp_path = conf
    r = client.post("/api/secrets/dev", headers=AUTH, json={"key": "server.token", "value": NEW_TOKEN})
    assert r.status_code == 200 and NEW_TOKEN not in r.text
    assert r.json()["secret"] == {"key": "server.token", "set": True, "last4": "9876"}
    assert any("client" in n for n in r.json()["notes"])
    assert NEW_TOKEN in _file(tmp_path, "dev", "secrets.yaml")
    token = next(s for g in client.get("/api/settings/dev", headers=AUTH).json()["groups"] for s in g["settings"]
                 if s["key"] == "server.token")
    assert token["last4"] == "9876" and "value" not in token
    r = client.post("/api/secrets/dev", headers=AUTH, json={"key": "providers.anthropic_api_key", "clear": True})
    assert r.status_code == 200 and r.json()["secret"]["set"] is False
    assert client.post("/api/secrets/dev", headers=AUTH, json={"key": "server.token"}).status_code == 422
    assert client.post("/api/secrets/prod", headers=AUTH, json={"key": "server.token",
                                                                "value": NEW_TOKEN}).status_code == 409
    audit = (cfg.home / "audit.jsonl").read_text(encoding="utf-8")
    assert NEW_TOKEN not in audit and SECRETS["dev"]["server.token"] not in audit and '"(secret)"' in audit


def test_a_section_edit_and_a_promote(conf):
    client, runner, cfg, tmp_path = conf
    sec = client.get("/api/sections/dev/schedule", headers=AUTH).json()
    assert sec["text"] == "" and any(r["name"] == "morning-brief" for r in sec["merged"])
    text = "- name: morning-brief\n  enabled: false\n"
    pv = client.post("/api/sections/dev/schedule/preview", headers=AUTH, json={"text": text}).json()
    assert pv["ok"] and pv["changes"] == {"schedule": "(section edited)"}
    r = client.post("/api/sections/dev/schedule/apply", headers=AUTH, json={"text": text, "hashes": pv["hashes"]})
    assert r.status_code == 200
    merged = client.get("/api/sections/dev/schedule", headers=AUTH).json()["merged"]
    assert next(r for r in merged if r["name"] == "morning-brief")["enabled"] is False
    assert client.get("/api/sections/dev/crontab", headers=AUTH).status_code == 404
    bad = client.post("/api/sections/dev/schedule/preview", headers=AUTH, json={"text": "- name: x\n  at: 19:00\n"})
    assert not bad.json()["ok"]

    diffs = {d["key"] for d in client.get("/api/promote", headers=AUTH).json()["differences"]}
    assert "schedule" in diffs and "instance.env" not in diffs
    pv = client.post("/api/promote/preview", headers=AUTH, json={"keys": ["schedule"]}).json()
    assert pv["ok"] and pv["changes"] == {"schedule": "(section edited)"}
    body = {"keys": ["schedule"], "hashes": pv["hashes"]}
    assert client.post("/api/promote/apply", headers=AUTH, json=body).status_code == 409  # no confirmation
    r = client.post("/api/promote/apply", headers=AUTH, json=body | {"confirm": "prod"})
    assert r.status_code == 200 and "morning-brief" in _file(tmp_path, "prod")
    refused = client.post("/api/promote/preview", headers=AUTH, json={"keys": ["server.token"]}).json()
    assert not refused["ok"] and "never promoted" in refused["problems"][0]
    assert [a["action"] for a in _audit(client)][:3] == ["promote", "promote", "section"]


def test_every_action_is_refused_while_the_deploy_agent_holds_its_lock(conf):
    client, runner, cfg, tmp_path = conf
    pv = client.post("/api/settings/dev/preview", headers=AUTH, json={"changes": {"memory.window": "30"}}).json()
    before = _file(tmp_path, "dev")
    with open(cfg.agent_lock, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)  # the agent mid-deploy
        assert client.get("/api/alerts", headers=AUTH).json()["deploy_busy"] is True
        r = client.post("/api/settings/dev/apply", headers=AUTH, json={"changes": {"memory.window": "30"},
                                                                      "hashes": pv["hashes"]})
        assert r.status_code == 409 and "deploy step is running" in r.json()["detail"]
        r = client.post("/api/services/dev/server/restart", headers=AUTH, json={})
        assert r.status_code == 409 and "deploy step is running" in r.json()["detail"]
    assert _file(tmp_path, "dev") == before and runner.calls == []
    assert [a["result"].split(":")[0] for a in _audit(client)] == ["refused", "refused"]
    assert client.post("/api/services/dev/server/restart", headers=AUTH, json={}).status_code == 200
    assert _audit(client)[0] == _audit(client)[0] | {"action": "restart", "target": "server", "result": "done"}


def test_the_alerts_status_says_why_they_are_off(conf):
    client, *_ = conf
    status = client.get("/api/alerts", headers=AUTH).json()
    assert status["on"] is False and "chat_id" in status["why"]

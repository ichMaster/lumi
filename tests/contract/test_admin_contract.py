"""The admin panel's contract (ARCHITECTURE §Deployment — v2.10 LUMI-256, extended by v2.11 LUMI-263).

1. the admin token on every ``/api`` route (401 without it or with a wrong one);
2. no write path into a **data root** — every route is called over fixture instance folders, whose ``data/`` is
   fingerprinted unchanged (v2.11: the panel writes only ``settings.yaml`` / ``secrets.yaml`` / ``config-history/``);
3. no secret in any response **or in the audit log** — known secret values (the instances' ``secrets.yaml``, one
   planted into a log line, the admin token, and a value the panel itself sets) appear in no response body and not in
   ``audit.jsonl``;
4. an invalid value, a stale preview and an unconfirmed prod write never reach a file.
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from tests._admin_world import AUTH, all_secret_values, build_world

SET_SECRET = "tok-SET-BY-THE-PANEL-SECRET-4321"
CALLS = [
    ("GET", "/api/overview", None), ("GET", "/api/health", None), ("GET", "/api/services", None),
    ("POST", "/api/services/dev/server/restart", {}), ("POST", "/api/services/prod/server/restart", {}),
    ("POST", "/api/services/prod/server/restart", {"confirm": "prod"}),
    ("GET", "/api/usage?instance=prod", None), ("GET", "/api/usage?instance=dev&days=31", None),
    ("GET", "/api/logs?instance=prod&source=lumi", None), ("GET", "/api/logs?instance=dev&source=lumi", None),
    ("GET", "/api/logs?instance=dev&source=container:server", None),
    ("GET", "/api/logs?instance=prod&source=telegram-outbound&q=up", None),
    # v2.11 — "@dev" / "@prod" stand for that instance's current hashes (fetched just before the call)
    ("GET", "/api/settings/dev", None), ("GET", "/api/settings/prod", None),
    ("POST", "/api/settings/dev/preview", {"changes": {"memory.window": "30"}}),
    ("POST", "/api/settings/dev/apply", {"changes": {"memory.window": "30"}, "hashes": "@dev"}),
    ("POST", "/api/settings/prod/apply", {"changes": {"memory.window": "30"}, "hashes": "@prod"}),
    ("POST", "/api/secrets/dev", {"key": "telegram.token", "value": SET_SECRET}),
    ("POST", "/api/secrets/prod", {"key": "server.token", "value": SET_SECRET}),
    ("POST", "/api/secrets/dev", {"key": "providers.anthropic_api_key", "clear": True}),
    ("GET", "/api/history/dev", None), ("POST", "/api/history/dev/20990101-000000Z/rollback", {}),
    ("GET", "/api/sections/dev/schedule", None), ("GET", "/api/sections/prod/models", None),
    ("POST", "/api/sections/dev/schedule/preview", {"text": "- name: morning-brief\n  enabled: false\n"}),
    ("POST", "/api/sections/dev/schedule/apply", {"text": "- name: morning-brief\n  enabled: false\n",
                                                  "hashes": "@dev"}),
    ("GET", "/api/promote", None), ("POST", "/api/promote/preview", {"keys": ["memory.window", "server.token"]}),
    ("POST", "/api/promote/apply", {"keys": ["memory.window"], "hashes": "@prod"}),
    ("POST", "/api/promote/apply", {"keys": ["memory.window"], "hashes": "@prod", "confirm": "prod"}),
    ("GET", "/api/audit", None), ("GET", "/api/alerts", None),
]


@pytest.fixture
def world(tmp_path):
    app, cfg, runner, servers, health = build_world(tmp_path)
    health.run_once()
    return TestClient(app), app, cfg, tmp_path


def _fingerprint(root):
    return {str(p.relative_to(root)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*")) if p.is_file()}


def _call(client, method, path, body, headers):
    if isinstance(body, dict) and isinstance(body.get("hashes"), str):
        name = body["hashes"].removeprefix("@")
        body = body | {"hashes": client.get(f"/api/settings/{name}", headers=AUTH).json()["hashes"]}
    return client.request(method, path, headers=headers, json=body)


def test_1_every_api_route_needs_the_token(world):
    client, app, *_ = world
    api_routes = {r.path for r in app.routes if getattr(r, "path", "").startswith("/api")}
    called = {path.split("?")[0] for _, path, _ in CALLS}
    templates = {"/api/services/{instance}/{service}/{action}": "/api/services/dev/server/restart",
                 "/api/settings/{instance}": "/api/settings/dev",
                 "/api/settings/{instance}/preview": "/api/settings/dev/preview",
                 "/api/settings/{instance}/apply": "/api/settings/dev/apply",
                 "/api/secrets/{instance}": "/api/secrets/dev", "/api/history/{instance}": "/api/history/dev",
                 "/api/history/{instance}/{ts}/rollback": "/api/history/dev/20990101-000000Z/rollback",
                 "/api/sections/{instance}/{name}": "/api/sections/dev/schedule",
                 "/api/sections/{instance}/{name}/preview": "/api/sections/dev/schedule/preview",
                 "/api/sections/{instance}/{name}/apply": "/api/sections/dev/schedule/apply"}
    assert {templates.get(r, r) for r in api_routes} <= called  # a new route must join CALLS (and this contract)
    for method, path, body in CALLS:
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic x"},
                        {"Authorization": AUTH["Authorization"] + "x"}):
            r = client.request(method, path, headers=headers, json=None if isinstance(body, dict) and
                               isinstance(body.get("hashes"), str) else body)
            assert r.status_code == 401, (method, path, headers)


def test_2_no_route_writes_into_a_data_root(world):
    client, _, cfg, _ = world
    before = {i.name: _fingerprint(i.data) for i in cfg.instances}
    for method, path, body in CALLS:
        _call(client, method, path, body, AUTH)
    assert {i.name: _fingerprint(i.data) for i in cfg.instances} == before


def test_3_no_secret_in_any_response_or_the_audit_log(world):
    client, _, cfg, _ = world
    secrets = all_secret_values() + [SET_SECRET]
    for method, path, body in CALLS + [("GET", "/", None), ("GET", "/healthz", None)]:
        r = _call(client, method, path, body, AUTH)
        leaked = [s for s in secrets if s in r.text]
        assert leaked == [], (method, path, leaked)
    assert SET_SECRET in (cfg.instance("dev").root / "secrets.yaml").read_text()  # the set really happened
    audit = (cfg.home / "audit.jsonl").read_text(encoding="utf-8")
    assert [s for s in secrets if s in audit] == [] and '"(secret)"' in audit


@pytest.mark.parametrize("path, body", [
    ("/api/settings/dev/apply", {"changes": {"memory.window": "0"}, "hashes": "@dev"}),
    ("/api/settings/dev/apply", {"changes": {"persona.think_show": "loud"}, "hashes": "@dev"}),
    ("/api/settings/dev/apply", {"changes": {"memory.window": "30"}, "hashes": {}}),
    ("/api/settings/prod/apply", {"changes": {"memory.window": "30"}, "hashes": "@prod"}),
    ("/api/settings/prod/apply", {"changes": {"memory.window": "30"}, "hashes": "@prod", "confirm": "yes"}),
    ("/api/secrets/prod", {"key": "server.token", "value": SET_SECRET}),
    ("/api/secrets/dev", {"key": "server.token", "value": " spaced "}),
    ("/api/sections/dev/schedule/apply", {"text": "- name: x\n  at: 19:00\n", "hashes": "@dev"}),
    ("/api/promote/apply", {"keys": ["memory.window"], "hashes": "@prod"}),
    ("/api/promote/apply", {"keys": ["server.token"], "hashes": "@prod", "confirm": "prod"}),
])
def test_4_an_invalid_stale_or_unconfirmed_write_never_reaches_a_file(world, path, body):
    client, _, cfg, _ = world
    before = {i.name: _fingerprint(i.root) for i in cfg.instances}
    r = _call(client, "POST", path, body, AUTH)
    assert r.status_code == 409, r.text
    assert {i.name: _fingerprint(i.root) for i in cfg.instances} == before

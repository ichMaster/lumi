"""v2.10 LUMI-256 — the admin panel's contract (ARCHITECTURE §Admin panel).

1. the admin token on every ``/api`` route (401 without it or with a wrong one);
2. no write path into a data root — every route is called over fixture instance folders, which are fingerprinted
   unchanged;
3. no secret in any response — known secret values (the instances' ``secrets.yaml``, one planted into a log line,
   the admin token) appear in no response body.
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from tests._admin_world import AUTH, all_secret_values, build_world

CALLS = [
    ("GET", "/api/overview", None), ("GET", "/api/health", None), ("GET", "/api/services", None),
    ("POST", "/api/services/dev/server/restart", {}), ("POST", "/api/services/prod/server/restart", {}),
    ("POST", "/api/services/prod/server/restart", {"confirm": "prod"}),
    ("GET", "/api/usage?instance=prod", None), ("GET", "/api/usage?instance=dev&days=31", None),
    ("GET", "/api/logs?instance=prod&source=lumi", None), ("GET", "/api/logs?instance=dev&source=lumi", None),
    ("GET", "/api/logs?instance=dev&source=container:server", None),
    ("GET", "/api/logs?instance=prod&source=telegram-outbound&q=up", None),
]


@pytest.fixture
def world(tmp_path):
    app, cfg, runner, servers, health = build_world(tmp_path)
    health.run_once()
    return TestClient(app), app, cfg, tmp_path


def _fingerprint(root):
    return {str(p.relative_to(root)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_1_every_api_route_needs_the_token(world):
    client, app, *_ = world
    api_routes = {r.path for r in app.routes if getattr(r, "path", "").startswith("/api")}
    assert api_routes == {"/api/overview", "/api/health", "/api/services", "/api/services/{instance}/{service}/{action}",
                          "/api/usage", "/api/logs"}  # a new route must join CALLS (and this contract)
    for method, path, body in CALLS:
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic x"},
                        {"Authorization": AUTH["Authorization"] + "x"}):
            r = client.request(method, path, headers=headers, json=body)
            assert r.status_code == 401, (method, path, headers)


def test_2_no_route_writes_into_a_data_root(world):
    client, _, cfg, _ = world
    before = {i.name: _fingerprint(i.root) for i in cfg.instances}
    for method, path, body in CALLS:
        client.request(method, path, headers=AUTH, json=body)
    assert {i.name: _fingerprint(i.root) for i in cfg.instances} == before


def test_3_no_secret_in_any_response(world):
    client, *_ = world
    secrets = all_secret_values()
    for method, path, body in CALLS + [("GET", "/", None), ("GET", "/healthz", None)]:
        text = client.request(method, path, headers=AUTH, json=body).text
        leaked = [s for s in secrets if s in text]
        assert leaked == [], (method, path, leaked)

"""The deploy agent's smoke against the real server API (v2.7 review #4).

The agent's unit tests use a fake HTTP; this pins the seam between the agent and the server: if ``/v1/health`` or
``/v1/state`` changed shape or auth, every deploy would roll back while those tests stayed green. The agent's HTTP is
routed into the real ``server.app.create_app`` (a TestClient, the mock model) — no network, no systemd, no paid call.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.agent import Core
from core.llm import MockLLMClient
from scripts.deploy_agent import Agent
from server.app import create_app
from state.local_store import JsonRepository

_VERSION = "2.7.0"


def _server(tmp_path: Path, *, env: str, version: str, token: str):
    core = Core(llm=MockLLMClient(), repository=JsonRepository(tmp_path / "store.json"), canon="Ти — Лілі.",
                model="m")
    return TestClient(create_app(core, token=token, env=env, version=version))


def _instance(home: Path, token: str) -> None:
    (home / "dev" / "data").mkdir(parents=True)
    (home / "dev" / ".env").write_text(f"LUMI_SERVER_HOST=127.0.0.1\nLUMI_SERVER_PORT=8766\nLUMI_SERVER_TOKEN={token}\n")


def _run(cmd, cwd=None):
    if cmd[:3] == ["systemctl", "--user", "is-enabled"]:
        return 0, "enabled"
    if cmd[:3] == ["systemctl", "--user", "is-active"]:
        return 0, "active"
    if cmd[:3] == ["systemctl", "--user", "show"]:
        return 0, "inv-1"
    raise AssertionError(cmd)


def _smoke(tmp_path, *, server_env="dev", server_version=_VERSION, server_token="tok", env_token="tok"):
    home = tmp_path / "lumi"
    _instance(home, env_token)
    client = _server(tmp_path, env=server_env, version=server_version, token=server_token)
    seen: list[str] = []

    def http(url, headers):
        assert url.startswith("http://127.0.0.1:8766/v1/")  # the instance's own address, from its .env
        path = url.removeprefix("http://127.0.0.1:8766")
        seen.append(path)
        res = client.get(path, headers=headers)
        return res.status_code, (res.json() if res.status_code == 200 else None)

    agent = Agent(home=home, run=_run, http=http, sleep=lambda s: None, now=lambda: 0.0, out=lambda s: None)
    return agent._smoke("dev", _VERSION, "1970-01-01 00:00:00"), seen


def test_a_healthy_server_passes_the_smoke(tmp_path):
    reasons, seen = _smoke(tmp_path)
    assert reasons == []
    assert seen == ["/v1/health", "/v1/state", "/v1/health"]  # health, the authed state, health a minute later


@pytest.mark.parametrize(("kw", "reason"), [
    ({"env_token": "stale"}, "/v1/state answered 401"),
    ({"server_version": "2.6.0"}, "the server runs '2.6.0', not '2.7.0'"),
    ({"server_env": "prod"}, "the server says env='prod', not 'dev'"),
])
def test_the_smoke_reads_the_real_answers(tmp_path, kw, reason):
    reasons, _ = _smoke(tmp_path, **kw)
    assert reasons == [reason]

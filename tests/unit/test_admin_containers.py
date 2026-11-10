"""v2.10 LUMI-255 — the container seam: the Docker Engine API over a unix socket, the prod confirmation rule.

A fake Docker daemon (an HTTP server on a real unix socket in a short temp dir) serves fixture JSON for
``/containers/json`` and inspect, the 8-byte log framing, and records every request. No real Docker, no network.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import socketserver
import struct
import tempfile
import threading
import urllib.parse
from datetime import UTC, datetime

import pytest

from admin import containers as ct

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def _container(cid: str, project: str, service: str) -> dict:
    return {"Id": cid, "Labels": {"com.docker.compose.project": project, "com.docker.compose.service": service}}


CONTAINERS = [
    _container("aaa", "lumi-dev", "server"), _container("bbb", "lumi-dev", "telegram-inbound"),
    _container("ccc", "lumi-dev", "telegram-outbound"), _container("ppp", "lumi-prod", "server"),
    _container("zzz", "lumi-dev", "something-else"),
]
INSPECT = {
    "aaa": {"Name": "/lumi-dev-server-1", "RestartCount": 2, "Config": {"Labels": {"io.lumi.build": "sha-1234567"}},
            "State": {"Status": "running", "StartedAt": "2026-11-10T11:00:00.123456789Z"}},
    "bbb": {"Name": "/lumi-dev-telegram-inbound-1", "RestartCount": 0, "Config": {"Labels": {}},
            "State": {"Status": "exited", "ExitCode": 143, "StartedAt": "2026-11-10T10:00:00Z"}},
    "ccc": {"Name": "/lumi-dev-telegram-outbound-1", "RestartCount": 5, "Config": {"Labels": {}},
            "State": {"Status": "exited", "ExitCode": 1, "StartedAt": "2026-11-10T10:00:00Z"}},
    "ppp": {"Name": "/lumi-prod-server-1", "RestartCount": 0, "Config": {"Labels": {"io.lumi.build": "v2.10.0"}},
            "State": {"Status": "running", "StartedAt": "2026-11-10T09:00:00Z"}},
}


def _frame(stream: int, text: str) -> bytes:
    data = text.encode()
    return bytes([stream, 0, 0, 0]) + struct.pack(">I", len(data)) + data


class _Daemon(http.server.BaseHTTPRequestHandler):
    requests: list[tuple[str, str]] = []

    def log_message(self, *a):  # quiet
        pass

    def _send(self, status: int, body: bytes = b"", ctype: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        self.requests.append(("GET", self.path))
        url = urllib.parse.urlparse(self.path)
        if url.path == "/containers/json":
            label = json.loads(urllib.parse.parse_qs(url.query)["filters"][0])["label"][0]
            project = label.split("=", 1)[1]
            rows = [c for c in CONTAINERS if c["Labels"]["com.docker.compose.project"] == project]
            return self._send(200, json.dumps(rows).encode())
        parts = url.path.strip("/").split("/")
        if len(parts) == 3 and parts[2] == "json":
            return self._send(200, json.dumps(INSPECT[parts[1]]).encode())
        if len(parts) == 3 and parts[2] == "logs":
            return self._send(200, _frame(1, "line one\nline two\n") + _frame(2, "an error line\n"),
                              "application/vnd.docker.raw-stream")
        self._send(404)

    def do_POST(self):  # noqa: N802
        self.requests.append(("POST", self.path))
        self._send(204)


class _UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


@pytest.fixture
def docker():
    folder = tempfile.mkdtemp(prefix="dk", dir="/tmp" if os.path.isdir("/tmp") else None)  # AF_UNIX: short path
    path = os.path.join(folder, "docker.sock")
    _Daemon.requests = []
    server = _UnixServer(path, _Daemon)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    runner = ct.DockerRunner({"prod": "lumi-prod", "dev": "lumi-dev"}, ct.DockerAPI(path, timeout=5),
                             clock=lambda: NOW)
    yield runner, _Daemon.requests
    server.shutdown()
    server.server_close()
    shutil.rmtree(folder, ignore_errors=True)


def test_list_finds_the_services_by_label(docker):
    runner, _ = docker
    dev = runner.list("dev")
    assert set(dev) == set(ct.SERVICES)  # "something-else" is not one of ours
    assert dev["server"] == {"state": "running", "started_at": "2026-11-10T11:00:00.123456789Z", "uptime_s": 3600,
                             "restart_count": 2, "image": "sha-1234567", "name": "lumi-dev-server-1"}
    assert dev["telegram-inbound"]["state"] == "stopped"      # exit 143: a stop
    assert dev["telegram-outbound"]["state"] == "crashed"     # exit 1
    prod = runner.list("prod")
    assert prod["server"]["image"] == "v2.10.0" and prod["telegram-inbound"] == {"state": "missing"}
    assert runner.list("nope") == {}


def test_actions_go_to_the_right_container_with_the_grace(docker):
    runner, requests = docker
    assert runner.act("dev", "server", "restart")["result"] == "done"
    runner.act("dev", "telegram-inbound", "start")
    runner.act("prod", "server", "stop", confirm="prod")
    posts = [p for m, p in requests if m == "POST"]
    assert posts == ["/containers/aaa/restart?t=300", "/containers/bbb/start", "/containers/ppp/stop?t=300"]


@pytest.mark.parametrize("args, why", [
    (("prod", "server", "restart", None), "type 'prod'"),
    (("prod", "server", "restart", "yes"), "type 'prod'"),
    (("dev", "store", "restart", None), "unknown service"),
    (("dev", "server", "kill", None), "unknown action"),
    (("staging", "server", "restart", None), "unknown instance"),
])
def test_a_refused_action_never_reaches_docker(docker, args, why):
    runner, requests = docker
    with pytest.raises(ct.Refused, match=why):
        runner.act(*args)
    assert not [r for r in requests if r[0] == "POST"]


def test_a_missing_container_is_refused_readably(docker):
    runner, _ = docker
    with pytest.raises(ct.Refused, match="no container"):
        runner.act("prod", "telegram-inbound", "start", confirm="prod")


def test_logs_are_demultiplexed(docker):
    runner, requests = docker
    assert runner.logs("dev", "server", tail=50) == ["line one", "line two", "an error line"]
    assert ("GET", "/containers/aaa/logs?stdout=1&stderr=1&tail=50") in requests
    assert runner.logs("dev", "nope") == [] and runner.logs("x", "server") == []


def test_docker_unreachable_is_unknown_never_an_exception():
    runner = ct.DockerRunner({"dev": "lumi-dev"}, ct.DockerAPI("/tmp/no-such-docker.sock", timeout=1))
    assert runner.list("dev") == {s: {"state": "unknown"} for s in ct.SERVICES}
    assert runner.logs("dev", "server") == []


@pytest.mark.parametrize("instance, confirm, ok", [
    ("prod", None, False), ("prod", "", False), ("prod", "Prod", False), ("prod", " prod ", True),
    ("dev", None, True), ("dev", "anything", True),
])
def test_the_prod_rule(instance, confirm, ok):
    if ok:
        ct.check_action(instance, "server", "restart", confirm, instances={"prod", "dev"})
    else:
        with pytest.raises(ct.Refused):
            ct.check_action(instance, "server", "restart", confirm, instances={"prod", "dev"})


def test_demux_reads_a_tty_stream_as_plain_text():
    assert ct.demux_logs(b"plain\ntext\n") == ["plain", "text"]


def test_the_fake_runner_follows_the_same_rule():
    fake = ct.FakeRunner(["prod", "dev"])
    fake.act("dev", "server", "stop")
    assert fake.list("dev")["server"]["state"] == "stopped"
    with pytest.raises(ct.Refused):
        fake.act("prod", "server", "restart")
    fake.act("prod", "server", "restart", confirm="prod")
    assert fake.list("prod")["server"]["restart_count"] == 1

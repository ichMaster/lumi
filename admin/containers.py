"""The container seam (v2.10, LUMI-255): status / start / stop / restart / logs of an instance's services.

One real implementation, :class:`DockerRunner`: the Docker Engine API over ``/var/run/docker.sock`` (stdlib
``http.client`` over a unix socket — no new dependency). Containers are found by their compose labels
(``com.docker.compose.project`` + ``…service``), never by a name built from a request; only the three known
services and the three actions exist. :func:`check_action` is the pure rule every action passes first — an action
on **prod** needs the typed confirmation ``prod``. Docker unreachable → ``unknown``, never an exception to the
caller of :meth:`DockerRunner.list`.
"""

from __future__ import annotations

import http.client
import json
import socket
import struct
import urllib.parse
from datetime import UTC, datetime
from typing import Any, Protocol

SERVICES = ("server", "telegram-inbound", "telegram-outbound")
ACTIONS = ("start", "stop", "restart")
PROD_CONFIRM = "prod"
STOP_GRACE_S = 300  # the compose stop_grace_period: SIGTERM → her session close (summary + facts) → exit
CLEAN_EXITS = {0, 137, 143}  # a clean exit, SIGKILL after the grace, SIGTERM — all a stop, not a crash
LOG_TAIL_MAX = 2000


class Refused(Exception):
    """An action the panel never sends to Docker; ``str(exc)`` says why (and what to type, for prod)."""


def check_action(instance: str, service: str, action: str, confirm: str | None, *, instances: set[str]) -> None:
    """Raise :class:`Refused` unless the action is allowed: a known instance, service and action — and, on prod,
    the typed confirmation ``prod``. Pure: plain data in, nothing sent anywhere."""
    if instance not in instances:
        raise Refused(f"unknown instance {instance!r}")
    if service not in SERVICES:
        raise Refused(f"unknown service {service!r} — one of {', '.join(SERVICES)}")
    if action not in ACTIONS:
        raise Refused(f"unknown action {action!r} — one of {', '.join(ACTIONS)}")
    if instance == "prod" and (confirm or "").strip() != PROD_CONFIRM:
        raise Refused(f"{action} on prod needs the typed confirmation — type {PROD_CONFIRM!r}")


class ContainerRunner(Protocol):
    def list(self, instance: str) -> dict[str, dict]: ...
    def act(self, instance: str, service: str, action: str, confirm: str | None = None) -> dict: ...
    def logs(self, instance: str, service: str, tail: int = 200) -> list[str]: ...


# --- the Docker Engine API over a unix socket -------------------------------------------------------------------


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._path)
        self.sock = sock


class DockerAPI:
    """A minimal Docker Engine API client: one request per call, over the daemon's unix socket."""

    def __init__(self, socket_path: str = "/var/run/docker.sock", timeout: float = 10.0) -> None:
        self.socket_path = socket_path
        self.timeout = timeout

    def request(self, method: str, path: str, *, timeout: float | None = None) -> tuple[int, bytes]:
        conn = _UnixConnection(self.socket_path, timeout or self.timeout)
        try:
            conn.request(method, path, headers={"Host": "docker"})
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def json(self, path: str) -> Any:
        status, body = self.request("GET", path)
        if status != 200:
            raise OSError(f"docker GET {path} → {status}")
        return json.loads(body)


def demux_logs(raw: bytes) -> list[str]:
    """Docker's multiplexed log stream (8-byte frames: stream, 0, 0, 0, size big-endian) → text lines. A stream
    without frames (a TTY container) is read as plain text."""
    out: list[bytes] = []
    i = 0
    if len(raw) >= 8 and raw[0] in (0, 1, 2) and raw[1:4] == b"\0\0\0":
        while i + 8 <= len(raw):
            size = struct.unpack(">I", raw[i + 4:i + 8])[0]
            out.append(raw[i + 8:i + 8 + size])
            i += 8 + size
        text = b"".join(out)
    else:
        text = raw
    return text.decode("utf-8", errors="replace").splitlines()


def _state(inspect: dict) -> str:
    st = inspect.get("State") or {}
    status = st.get("Status")
    if status == "running":
        return "running"
    if status == "restarting" or st.get("Restarting"):
        return "restarting"
    if status in ("exited", "dead"):
        return "stopped" if st.get("ExitCode") in CLEAN_EXITS else "crashed"
    return "stopped" if status in ("created", "paused") else "unknown"


def _uptime(started_at: str | None, now: datetime) -> int | None:
    if not started_at or started_at.startswith("0001"):
        return None
    try:
        start = datetime.fromisoformat(started_at[:19] + "+00:00")
    except ValueError:
        return None
    return max(0, int((now - start).total_seconds()))


class DockerRunner:
    """The panel's runner: each instance's compose project → its three services, through :class:`DockerAPI`."""

    def __init__(self, projects: dict[str, str], api: DockerAPI | None = None, *, clock=lambda: datetime.now(UTC)):
        self.projects = projects  # instance → compose project (lumi-prod / lumi-dev)
        self.api = api or DockerAPI()
        self.clock = clock

    def _containers(self, instance: str) -> dict[str, dict]:
        project = self.projects[instance]
        filters = urllib.parse.quote(json.dumps({"label": [f"com.docker.compose.project={project}"]}))
        out: dict[str, dict] = {}
        for c in self.api.json(f"/containers/json?all=1&filters={filters}"):
            labels = c.get("Labels") or {}
            if labels.get("com.docker.compose.project") != project:
                continue  # trust the labels we read, not only the filter
            service = labels.get("com.docker.compose.service")
            if service in SERVICES:
                out[service] = c
        return out

    def list(self, instance: str) -> dict[str, dict]:
        """Per service: state, started_at, uptime_s, restart_count, image tag — ``unknown`` when Docker can't say."""
        if instance not in self.projects:
            return {}
        try:
            found = self._containers(instance)
        except (OSError, ValueError, http.client.HTTPException):
            return {s: {"state": "unknown"} for s in SERVICES}
        now = self.clock()
        out: dict[str, dict] = {}
        for service in SERVICES:
            c = found.get(service)
            if c is None:
                out[service] = {"state": "missing"}
                continue
            try:
                ins = self.api.json(f"/containers/{c['Id']}/json")
            except (OSError, ValueError, http.client.HTTPException):
                out[service] = {"state": "unknown"}
                continue
            started = (ins.get("State") or {}).get("StartedAt")
            labels = (ins.get("Config") or {}).get("Labels") or {}
            out[service] = {"state": _state(ins), "started_at": started, "uptime_s": _uptime(started, now),
                            "restart_count": ins.get("RestartCount"), "image": labels.get("io.lumi.build"),
                            "name": (ins.get("Name") or "").lstrip("/")}
        return out

    def act(self, instance: str, service: str, action: str, confirm: str | None = None) -> dict:
        check_action(instance, service, action, confirm, instances=set(self.projects))
        c = self._containers(instance).get(service)
        if c is None:
            raise Refused(f"{instance}/{service} has no container to {action} (recreate it with compose)")
        grace = f"?t={STOP_GRACE_S}" if action in ("stop", "restart") else ""
        status, body = self.api.request("POST", f"/containers/{c['Id']}/{action}{grace}",
                                        timeout=STOP_GRACE_S + 30)
        if status not in (204, 304):
            raise OSError(f"docker {action} {instance}/{service} → {status}: {body[:200]!r}")
        return {"instance": instance, "service": service, "action": action,
                "result": "done" if status == 204 else "already so"}

    def logs(self, instance: str, service: str, tail: int = 200) -> list[str]:
        if instance not in self.projects or service not in SERVICES:
            return []
        try:
            c = self._containers(instance).get(service)
            if c is None:
                return []
            n = max(1, min(int(tail), LOG_TAIL_MAX))
            status, body = self.api.request("GET", f"/containers/{c['Id']}/logs?stdout=1&stderr=1&tail={n}")
        except (OSError, ValueError, http.client.HTTPException):
            return []
        return demux_logs(body) if status == 200 else []


class FakeRunner:
    """An in-memory runner for tests and local runs: the three services per instance, start / stop / restart."""

    def __init__(self, instances: list[str]) -> None:
        self.state = {(i, s): "running" for i in instances for s in SERVICES}
        self.restarts = {(i, s): 0 for i in instances for s in SERVICES}
        self.calls: list[tuple[str, str, str]] = []
        self.log_lines: dict[tuple[str, str], list[str]] = {}

    def list(self, instance: str) -> dict[str, dict]:
        if not any(i == instance for i, _ in self.state):
            return {}
        return {s: {"state": self.state[(instance, s)], "restart_count": self.restarts[(instance, s)],
                    "image": "test", "started_at": None, "uptime_s": None, "name": f"lumi-{instance}-{s}-1"}
                for s in SERVICES}

    def act(self, instance: str, service: str, action: str, confirm: str | None = None) -> dict:
        check_action(instance, service, action, confirm, instances={i for i, _ in self.state})
        self.calls.append((instance, service, action))
        self.state[(instance, service)] = "stopped" if action == "stop" else "running"
        if action == "restart":
            self.restarts[(instance, service)] += 1
        return {"instance": instance, "service": service, "action": action, "result": "done"}

    def logs(self, instance: str, service: str, tail: int = 200) -> list[str]:
        return self.log_lines.get((instance, service), [])[-tail:]

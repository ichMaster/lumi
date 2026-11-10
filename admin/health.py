"""The health checks + the every-minute runner (v2.10, LUMI-254).

Each check is a pure function of the gathered :class:`Facts` → a :class:`CheckResult` (``green`` / ``amber`` /
``red``, or ``off`` when the feature is off). The facts come from the read-only readers (``admin/readers.py``), two
HTTP probes of the instance's server (``GET /v1/health``, ``GET /v1/state`` with its token) and the container
states. **No paid call**: the model is judged from the ledgers only. The runner appends one row per instance per run
to ``health.jsonl`` in the panel's own folder (never a data root), rotated at a size cap, and keeps a short
history per check for the UI.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from admin import readers as rd

GREEN, AMBER, RED, OFF = "green", "amber", "red", "off"

SERVER_SLOW_MS = 2000            # /v1/health slower than this → amber
MOOD_DUE_HOUR = 12               # no mood reading yet before noon → amber; after → red
MODEL_FAILS_AMBER = 1            # model failures / timeouts in the last hour
MODEL_FAILS_RED = 3
MODEL_STALE_S = 12 * 3600        # the last recorded model call older than this (while the server runs) → amber
BUS_PENDING_AMBER = 3            # inbox / outbox records waiting
BUS_PENDING_RED = 20
SCHED_AMBER_S = 6 * 3600         # the newest scheduled firing older than this → amber
SCHED_RED_S = 24 * 3600
DISK_AMBER = 0.10                # free space below this fraction → amber
DISK_RED = 0.05
CHECKS = ("server", "state", "mood", "model", "telegram", "scheduler", "disk", "marker")

# (url, headers) → (HTTP status or 0, parsed JSON or None, elapsed ms)
Http = Callable[[str, dict], tuple[int, Any, float]]


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str = ""


@dataclass
class Facts:
    """Everything the checks read, gathered once per instance per run."""

    instance: str
    health: dict | None = None
    health_ms: float | None = None
    health_error: str = ""
    state_status: int | None = None   # None → no token to ask with
    mood: dict = field(default_factory=dict)
    hour: int = 0
    last_call_age_s: int | None = None
    model_failures_1h: int = 0
    services: dict = field(default_factory=dict)   # service → state (running / stopped / …)
    bus: dict = field(default_factory=dict)
    scheduler: dict = field(default_factory=dict)
    disk_free: float | None = None
    marker: str | None = None


# --- the checks (pure) ------------------------------------------------------------------------------------------


def check_server(f: Facts) -> CheckResult:
    if f.health is None:
        return CheckResult("server", RED, f"down — {f.health_error or 'no answer'}")
    if f.health.get("env") != f.instance:
        return CheckResult("server", RED, f"answers as {f.health.get('env')!r}, not {f.instance!r}")
    detail = f"v{f.health.get('version') or '?'} · {round(f.health_ms or 0)} ms"
    return CheckResult("server", AMBER if (f.health_ms or 0) > SERVER_SLOW_MS else GREEN, detail)


def check_state(f: Facts) -> CheckResult:
    if f.state_status is None:
        return CheckResult("state", AMBER, "no server.token in the instance's secrets.yaml to ask with")
    if f.state_status == 200:
        return CheckResult("state", GREEN, "readable")
    return CheckResult("state", RED, f"/v1/state → {f.state_status or 'no answer'}")


def check_mood(f: Facts) -> CheckResult:
    if f.mood.get("present") and f.mood.get("complete"):
        return CheckResult("mood", GREEN, f"complete · {f.mood.get('theme') or 'no theme'}")
    if f.mood.get("present"):
        return CheckResult("mood", RED, "today's reading is incomplete")
    return CheckResult("mood", AMBER if f.hour < MOOD_DUE_HOUR else RED, "no reading yet today")


def check_model(f: Facts) -> CheckResult:
    if f.services.get("server") not in (None, "running"):
        return CheckResult("model", OFF, "the server isn't running")
    fails = f.model_failures_1h
    if fails >= MODEL_FAILS_RED:
        return CheckResult("model", RED, f"{fails} failures / timeouts in the last hour")
    if f.last_call_age_s is None:
        return CheckResult("model", AMBER, "no model call recorded")
    age = f"last call {f.last_call_age_s // 60} min ago"
    if fails >= MODEL_FAILS_AMBER:
        return CheckResult("model", AMBER, f"{fails} failure(s) in the last hour · {age}")
    return CheckResult("model", AMBER if f.last_call_age_s > MODEL_STALE_S else GREEN, age)


def check_telegram(f: Facts) -> CheckResult:
    if not f.bus.get("bridge"):
        return CheckResult("telegram", OFF, "the bridge is off")
    down = [s for s in ("telegram-inbound", "telegram-outbound") if f.services.get(s) != "running"]
    if down:
        return CheckResult("telegram", RED, ", ".join(f"{s} {f.services.get(s) or 'unknown'}" for s in down))
    waiting = max(f.bus.get("inbox_pending") or 0, f.bus.get("outbox_pending") or 0)
    detail = f"inbox {f.bus.get('inbox_pending')} · outbox {f.bus.get('outbox_pending')} waiting"
    if waiting > BUS_PENDING_RED:
        return CheckResult("telegram", RED, detail)
    return CheckResult("telegram", AMBER if waiting > BUS_PENDING_AMBER else GREEN, detail)


def check_scheduler(f: Facts) -> CheckResult:
    if not f.scheduler.get("enabled"):
        return CheckResult("scheduler", OFF, "the scheduler is off")
    age = f.scheduler.get("age_s")
    if age is None:
        return CheckResult("scheduler", AMBER, "no firing recorded")
    status = RED if age > SCHED_RED_S else AMBER if age > SCHED_AMBER_S else GREEN
    return CheckResult("scheduler", status, f"last firing {age // 60} min ago")


def check_disk(f: Facts) -> CheckResult:
    if f.disk_free is None:
        return CheckResult("disk", AMBER, "unknown")
    status = RED if f.disk_free < DISK_RED else AMBER if f.disk_free < DISK_AMBER else GREEN
    return CheckResult("disk", status, f"{round(f.disk_free * 100)} % free")


def check_marker(f: Facts) -> CheckResult:
    if f.marker is None:
        return CheckResult("marker", AMBER, "the data root has no env-marker")
    if f.marker != f.instance:
        return CheckResult("marker", RED, f"marked {f.marker!r}, not {f.instance!r}")
    return CheckResult("marker", GREEN, f.marker)


_CHECKS = (check_server, check_state, check_mood, check_model, check_telegram, check_scheduler, check_disk,
           check_marker)


def evaluate(f: Facts) -> list[CheckResult]:
    return [check(f) for check in _CHECKS]


# --- gathering the facts ----------------------------------------------------------------------------------------


def urllib_http(url: str, headers: dict, *, timeout: float = 5.0) -> tuple[int, Any, float]:
    """A GET with a short timeout → ``(status, json or None, elapsed ms)``; any failure → status 0."""
    start = time.monotonic()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
            body = resp.read(1_000_000)
            status = resp.status
    except urllib.error.HTTPError as exc:
        return exc.code, None, (time.monotonic() - start) * 1000
    except (OSError, ValueError):
        return 0, None, (time.monotonic() - start) * 1000
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    return status, data, (time.monotonic() - start) * 1000


def gather(inst: rd.Instance, now: datetime, *, http: Http, services: Callable[[rd.Instance], dict]) -> Facts:
    """The facts for one instance: two probes of its server (no model call), the readers, its containers."""
    base = inst.url.rstrip("/")
    status, body, ms = http(f"{base}/v1/health", {})
    f = Facts(instance=inst.name, hour=now.hour)
    if status == 200 and isinstance(body, dict):
        f.health, f.health_ms = body, ms
    else:
        f.health_error = f"HTTP {status}" if status else "unreachable"
    token = rd.server_token(inst)
    if token:
        f.state_status = http(f"{base}/v1/state", {"Authorization": f"Bearer {token}"})[0]
    f.mood = rd.mood_today(inst, today=now.date().isoformat())
    u = rd.errors(inst, now=now)
    f.model_failures_1h = u["model_failures_1h"]
    f.last_call_age_s = rd._last_call_age(inst, now)
    f.services = {name: info.get("state") for name, info in (services(inst) or {}).items()}
    f.bus = rd.bus(inst)
    f.scheduler = rd.scheduler(inst, now=now)
    disks = rd.box([inst.root])["disks"]
    disk = disks.get(str(inst.root))
    f.disk_free = disk["free"] / disk["total"] if disk and disk["total"] else None
    f.marker = rd.marker(inst)
    return f


# --- the runner -------------------------------------------------------------------------------------------------


class HealthRunner:
    """Evaluate every instance's checks (every minute in :meth:`loop`); append ``health.jsonl``; keep a history."""

    def __init__(self, instances: list[rd.Instance], history_path: Path, *, http: Http = urllib_http,
                 services: Callable[[rd.Instance], dict] = lambda inst: {},
                 clock: Callable[[], datetime] = lambda: datetime.now().astimezone(),
                 keep: int = 60, max_bytes: int = 5_000_000) -> None:
        self.instances = instances
        self.history_path = Path(history_path)
        self.http = http
        self.services = services
        self.clock = clock
        self.max_bytes = max_bytes
        self.latest: dict[str, list[CheckResult]] = {}
        self.history: dict[tuple[str, str], deque] = {}
        self.keep = keep
        self.last_run: str | None = None

    def run_once(self) -> dict[str, list[CheckResult]]:
        now = self.clock()
        for inst in self.instances:
            try:
                results = evaluate(gather(inst, now, http=self.http, services=self.services))
            except Exception as exc:  # noqa: BLE001 — one instance's surprise never stops the others' checks
                results = [CheckResult("server", RED, f"the checks failed: {type(exc).__name__}")]
            self.latest[inst.name] = results
            for r in results:
                self.history.setdefault((inst.name, r.name), deque(maxlen=self.keep)).append(
                    (now.isoformat(timespec="seconds"), r.status))
            self._append({"ts": now.isoformat(timespec="seconds"), "instance": inst.name,
                          "checks": {r.name: r.status for r in results},
                          "details": {r.name: r.detail for r in results}})
        self.last_run = now.isoformat(timespec="seconds")
        return self.latest

    def _append(self, row: dict) -> None:
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            if self.history_path.exists() and self.history_path.stat().st_size > self.max_bytes:
                self.history_path.replace(self.history_path.with_name(self.history_path.name + ".1"))
            with self.history_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass  # the history is best-effort; the checks still show

    def snapshot(self) -> dict[str, Any]:
        """The latest results + each check's history, for the API."""
        return {"last_run": self.last_run, "instances": {
            name: [asdict(r) | {"history": list(self.history.get((name, r.name), ()))} for r in results]
            for name, results in self.latest.items()}}

    async def loop(self, interval: float = 60.0) -> None:
        while True:
            await asyncio.to_thread(self.run_once)
            await asyncio.sleep(interval)

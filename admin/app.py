"""The admin panel's FastAPI app (v2.10, LUMI-256): a JSON API + the static page.

Every ``/api`` route needs ``Authorization: Bearer <admin token>``; only ``/`` (the static page), ``/static/*`` and
``/healthz`` are open. The panel never runs the core and never writes a data root — it reads through
``admin/readers.py``, asks each server (``/v1/health``, ``/v1/state`` with its token), and drives containers through
the ``ContainerRunner`` seam, where a prod action needs the typed confirmation. No response carries a secret: the
log lines are redacted, and the server state is reduced to the fields the panel shows.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from admin import containers as ct
from admin import health as hl
from admin import readers as rd
from admin.registry import AdminConfig

STATIC = Path(__file__).resolve().parent / "static"
STATE_FIELDS = ("model", "provider", "profile", "mood", "theme", "session_id", "think_show", "stream_enabled")


def _state_view(state: Any) -> dict[str, Any]:
    """The few fields of an instance's ``/v1/state`` the panel shows — never the whole payload."""
    if not isinstance(state, dict):
        return {}
    out = {k: state.get(k) for k in STATE_FIELDS}
    totals = state.get("totals") if isinstance(state.get("totals"), dict) else {}
    out["session_turns"] = totals.get("turns")
    return out


def create_app(cfg: AdminConfig, runner: ct.ContainerRunner, health: hl.HealthRunner, *,
               http: hl.Http = hl.urllib_http, clock: Callable[[], datetime] = lambda: datetime.now().astimezone(),
               background: bool = True, proc: Path = Path("/proc")) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(health.loop()) if background else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()

    app = FastAPI(title="Lumi admin", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    def require_token(authorization: str = Header(default="")) -> None:
        scheme, _, given = authorization.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(given.strip().encode(), cfg.token.encode()):
            raise HTTPException(status_code=401, detail="the admin token is missing or wrong")

    guarded = [Depends(require_token)]

    def instance_or_404(name: str) -> rd.Instance:
        inst = cfg.instance(name)
        if inst is None:
            raise HTTPException(status_code=404, detail=f"unknown instance {name!r}")
        return inst

    # --- open ---------------------------------------------------------------------------------------------------

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    # --- the API (the token on every route) ---------------------------------------------------------------------

    @app.get("/api/overview", dependencies=guarded)
    def overview() -> dict:
        now = clock()
        out = []
        for inst in cfg.instances:
            status, body, ms = http(f"{inst.url}/v1/health", {})
            healthy = status == 200 and isinstance(body, dict)
            state: dict = {}
            token = rd.server_token(inst)
            if healthy and token:
                s_status, s_body, _ = http(f"{inst.url}/v1/state", {"Authorization": f"Bearer {token}"})
                state = _state_view(s_body) if s_status == 200 else {}
            services = runner.list(inst.name)
            server = services.get("server", {})
            usage = rd.usage(inst, now=now, days=1)
            out.append({
                "name": inst.name,
                "up": healthy and server.get("state") in ("running", None),
                "version": body.get("version") if healthy else None,
                "health_ms": round(ms) if healthy else None,
                "image": server.get("image"),
                "uptime_s": server.get("uptime_s"),
                "restarts": {name: info.get("restart_count") for name, info in services.items()},
                "services": {name: info.get("state") for name, info in services.items()},
                "state": state,
                "mood_today": rd.mood_today(inst, today=now.date().isoformat()),
                "turns_today": usage["turns_today"],
                "today": usage["today"],
                "latency": usage["latency"],
                "last_turn_age_s": usage["last_turn_age_s"],
                "errors": rd.errors(inst, now=now),
                "data_size": rd.data_size(inst),
            })
        return {"now": now.isoformat(timespec="seconds"), "instances": out,
                "box": rd.box([i.root for i in cfg.instances], proc=proc)}

    @app.get("/api/health", dependencies=guarded)
    def health_view() -> dict:
        return health.snapshot()

    @app.get("/api/services", dependencies=guarded)
    def services() -> dict:
        return {inst.name: runner.list(inst.name) for inst in cfg.instances}

    @app.post("/api/services/{instance}/{service}/{action}", dependencies=guarded)
    def act(instance: str, service: str, action: str, payload: dict = Body(default={})) -> dict:  # noqa: B008
        instance_or_404(instance)
        confirm = payload.get("confirm") if isinstance(payload, dict) else None
        try:
            return runner.act(instance, service, action, confirm)
        except ct.Refused as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except OSError as exc:
            raise HTTPException(status_code=502, detail=f"docker: {exc}") from None

    @app.get("/api/usage", dependencies=guarded)
    def usage(instance: str, days: int = Query(default=7, ge=1, le=31)) -> dict:
        return rd.usage(instance_or_404(instance), now=clock(), days=days)

    @app.get("/api/logs", dependencies=guarded)
    def logs(instance: str, source: str = "lumi", level: str = "", q: str = "",
             tail: int = Query(default=200, ge=1, le=2000)) -> dict:
        inst = instance_or_404(instance)
        if source.startswith("container:"):
            service = source.split(":", 1)[1]
            if service not in ct.SERVICES:
                raise HTTPException(status_code=404, detail=f"unknown service {service!r}")
            redact = rd.redactor(inst)
            lines = [redact(line) for line in runner.logs(inst.name, service, tail)]
            if q:
                lines = [line for line in lines if q.lower() in line.lower()]
            return {"source": source, "lines": [{"message": line} for line in lines]}
        if source not in rd.LOG_SOURCES:
            raise HTTPException(status_code=404, detail=f"unknown source {source!r}")
        return {"source": source, "lines": rd.log_tail(inst, source, level=level, q=q, tail=tail)}

    return app

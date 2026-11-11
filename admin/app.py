"""The admin panel's FastAPI app (v2.10, LUMI-256; configure v2.11, LUMI-263): a JSON API + the static page.

Every ``/api`` route needs ``Authorization: Bearer <admin token>``; only ``/`` (the static page), ``/static/*`` and
``/healthz`` are open. The panel never runs the core and never writes a data root — it reads through
``admin/readers.py``, asks each server (``/v1/health``, ``/v1/state`` with its token), and drives containers through
the ``ContainerRunner`` seam, where a prod action needs the typed confirmation. No response carries a secret: the
log lines are redacted, and the server state is reduced to the fields the panel shows.

v2.11: the panel **writes** an instance's ``settings.yaml`` / ``secrets.yaml`` / ``config-history/`` through
``admin/edit.py`` (never ``data/``). Every write — and every service action — runs inside the deploy agent's lock
(``admin/guard.py``), needs ``prod`` typed on prod, restarts the instance's running services, and is audited
(``admin/audit.py``) whether it is done, refused or failed. Secrets are write-only.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
from collections.abc import Callable
from datetime import datetime
from http.client import HTTPException as DockerProtocolError
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from admin import containers as ct
from admin import edit as ed
from admin import guard as gd
from admin import health as hl
from admin import readers as rd
from admin.alerts import Alerts
from admin.audit import AUDIT_FILE, Audit
from admin.registry import AdminConfig
from core import settings as st

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


def _dict(payload: Any, key: str) -> dict:
    value = payload.get(key) if isinstance(payload, dict) else None
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise HTTPException(status_code=422, detail=f"{key} must be a mapping")
    return value


def _str(payload: Any, key: str) -> str | None:
    value = payload.get(key) if isinstance(payload, dict) else None
    if value is not None and not isinstance(value, str):
        raise HTTPException(status_code=422, detail=f"{key} must be text")
    return value


def create_app(cfg: AdminConfig, runner: ct.ContainerRunner, health: hl.HealthRunner, *,
               http: hl.Http = hl.urllib_http, clock: Callable[[], datetime] = lambda: datetime.now().astimezone(),
               background: bool = True, proc: Path = Path("/proc"), audit: Audit | None = None,
               alerts: Alerts | None = None) -> FastAPI:
    audit = audit or Audit(cfg.home / AUDIT_FILE, clock=clock)
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

    def redact_all(text: str) -> str:
        for inst in cfg.instances:
            text = rd.redactor(inst)(text)
        return text

    def record(action: str, instance: str | None, target: str, result: str, changes: dict | None = None) -> None:
        audit.record(action, instance, target, changes=changes, result=result, redact=redact_all)

    @app.post("/api/services/{instance}/{service}/{action}", dependencies=guarded)
    def act(instance: str, service: str, action: str, payload: dict = Body(default={})) -> dict:  # noqa: B008
        instance_or_404(instance)
        confirm = _str(payload, "confirm")
        try:
            with gd.agent_lock(cfg.agent_lock):
                out = runner.act(instance, service, action, confirm)
        except (ct.Refused, gd.Busy) as exc:
            record(action, instance, service, f"refused: {exc}")
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except (OSError, ValueError, DockerProtocolError) as exc:  # code review #2: every Docker error
            record(action, instance, service, f"failed: docker: {exc}")
            raise HTTPException(status_code=502, detail=f"docker: {exc}") from None
        record(action, instance, service, "done")
        return out

    # --- v2.11: configure (every write audited, lock-guarded, prod confirmed) -----------------------------------

    def restart_running(instance: str, confirm: str | None) -> dict[str, str]:
        """Restart the instance's running services so they read the new files; a stopped one stays stopped. The files
        are already written here, so every Docker error is that service's ``failed: …`` — never an exception that
        would skip the audit line (code review #2)."""
        out: dict[str, str] = {}
        try:
            states = runner.list(instance)
        except Exception as exc:  # noqa: BLE001 — reported per service, never raised past the written files
            return {s: f"failed: docker: {exc}" for s in ct.SERVICES}
        for service in ct.SERVICES:
            state = (states.get(service) or {}).get("state")
            if state in (None, "unknown"):
                out[service] = "failed: Docker can't say whether it runs — restart it from Services"
                continue
            if state != "running":
                out[service] = "not running — reads the new settings when started"
                continue
            try:
                runner.act(instance, service, "restart", confirm)
                out[service] = "restarted"
            except Exception as exc:  # noqa: BLE001 — as above
                out[service] = f"failed: {exc}"
        return out

    def write(inst: rd.Instance, action: str, target: str, confirm: str | None, build: Callable[[], ed.Plan],
              expect: dict | None) -> dict:
        """Confirm (prod) → the agent lock → plan → apply (history first) → restart → audit; a refusal is a 409."""
        try:
            ed.check_confirm(inst.name, confirm, f"this {action}")
            with gd.agent_lock(cfg.agent_lock):
                plan = build()
                if not plan.ok:
                    raise ed.EditError("; ".join(plan.public()["problems"]))
                result = ed.apply(plan, expect if expect is not None else ed.hashes(plan.old))
                restarted = restart_running(inst.name, confirm) if result["restart"] else {}
        except (ed.EditError, gd.Busy) as exc:
            record(action, inst.name, target, f"refused: {exc}")
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except OSError as exc:
            record(action, inst.name, target, f"failed: {exc.strerror or type(exc).__name__}")
            raise HTTPException(status_code=502, detail=f"the write failed: {exc.strerror or exc}") from None
        failed = {s: r for s, r in restarted.items() if r.startswith("failed")}
        record(action, inst.name, target, "done" + (f"; restart {failed}" if failed else ""), result["changes"])
        return result | {"restarted": restarted}

    @app.get("/api/settings/{instance}", dependencies=guarded)
    def settings_view(instance: str) -> dict:
        inst = instance_or_404(instance)
        return ed.view(inst.root) | {"instance": inst.name, "deploy_busy": gd.agent_busy(cfg.agent_lock)}

    @app.post("/api/settings/{instance}/preview", dependencies=guarded)
    def settings_preview(instance: str, payload: dict = Body(default={})) -> dict:  # noqa: B008
        return ed.plan_settings(instance_or_404(instance).root, _dict(payload, "changes")).public()

    @app.post("/api/settings/{instance}/apply", dependencies=guarded)
    def settings_apply(instance: str, payload: dict = Body(default={})) -> dict:  # noqa: B008
        inst = instance_or_404(instance)
        changes = _dict(payload, "changes")
        return write(inst, "settings", ", ".join(changes), _str(payload, "confirm"),
                     lambda: ed.plan_settings(inst.root, changes), _dict(payload, "hashes"))

    @app.post("/api/secrets/{instance}", dependencies=guarded)
    def secret_set(instance: str, payload: dict = Body(default={})) -> dict:  # noqa: B008
        inst = instance_or_404(instance)
        key = _str(payload, "key") or ""
        value = None if payload.get("clear") is True else _str(payload, "value")
        if value is None and payload.get("clear") is not True:
            raise HTTPException(status_code=422, detail="give a value, or clear: true")
        out = write(inst, "secret clear" if value is None else "secret set", key, _str(payload, "confirm"),
                    lambda: ed.plan_secret(inst.root, key, value), _dict(payload, "hashes") or None)
        name = st.lookup(key).key  # the write succeeded, so the key is a known secret
        shown = next(s for g in ed.view(inst.root)["groups"] for s in g["settings"] if s["key"] == name)
        return out | {"secret": {"key": name, "set": shown["set"], "last4": shown["last4"]}}

    @app.get("/api/history/{instance}", dependencies=guarded)
    def history(instance: str, limit: int = Query(default=50, ge=1, le=200)) -> dict:
        return {"entries": ed.history(instance_or_404(instance).root, limit)}

    def rollback_plan_or_404(inst: rd.Instance, ts: str) -> ed.Plan:
        try:
            return ed.plan_rollback(inst.root, ts)
        except ed.EditError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    @app.post("/api/history/{instance}/{ts}/preview", dependencies=guarded)
    def rollback_preview(instance: str, ts: str) -> dict:
        """What a rollback would do — the diff, and the later changes it also undoes (code review #1)."""
        return rollback_plan_or_404(instance_or_404(instance), ts).public()

    @app.post("/api/history/{instance}/{ts}/rollback", dependencies=guarded)
    def rollback(instance: str, ts: str, payload: dict = Body(default={})) -> dict:  # noqa: B008
        inst = instance_or_404(instance)
        rollback_plan_or_404(inst, ts)
        return write(inst, "rollback", ts, _str(payload, "confirm"), lambda: ed.plan_rollback(inst.root, ts),
                     _dict(payload, "hashes") or None)

    def section_or_404(name: str) -> str:
        if name not in st.STRUCTURED:
            raise HTTPException(status_code=404, detail=f"unknown section {name!r}")
        return name

    @app.get("/api/sections/{instance}/{name}", dependencies=guarded)
    def section_view(instance: str, name: str) -> dict:
        inst, name = instance_or_404(instance), section_or_404(name)
        return {"name": name, "text": ed.section_text(inst.root, name), "merged": ed.merged_section(inst.root, name),
                "hashes": ed.hashes(ed.read_files(inst.root))}

    @app.post("/api/sections/{instance}/{name}/preview", dependencies=guarded)
    def section_preview(instance: str, name: str, payload: dict = Body(default={})) -> dict:  # noqa: B008
        inst, name = instance_or_404(instance), section_or_404(name)
        return ed.plan_section(inst.root, name, _str(payload, "text")).public()

    @app.post("/api/sections/{instance}/{name}/apply", dependencies=guarded)
    def section_apply(instance: str, name: str, payload: dict = Body(default={})) -> dict:  # noqa: B008
        inst, name = instance_or_404(instance), section_or_404(name)
        text = _str(payload, "text")
        return write(inst, "section", name, _str(payload, "confirm"), lambda: ed.plan_section(inst.root, name, text),
                     _dict(payload, "hashes"))

    def pair() -> tuple[rd.Instance, rd.Instance]:
        dev, prod = cfg.instance("dev"), cfg.instance("prod")
        if dev is None or prod is None:
            raise HTTPException(status_code=404, detail="promote needs both dev and prod in the registry")
        return dev, prod

    def keys_of(payload: Any) -> list[str]:
        keys = payload.get("keys") if isinstance(payload, dict) else None
        if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
            raise HTTPException(status_code=422, detail="keys must be a list of setting names")
        return keys

    @app.get("/api/promote", dependencies=guarded)
    def promote_view() -> dict:
        dev, prod = pair()
        return {"differences": ed.promotable(dev.root, prod.root), "hashes": ed.hashes(ed.read_files(prod.root))}

    @app.post("/api/promote/preview", dependencies=guarded)
    def promote_preview(payload: dict = Body(default={})) -> dict:  # noqa: B008
        dev, prod = pair()
        return ed.plan_promote(dev.root, prod.root, keys_of(payload)).public()

    @app.post("/api/promote/apply", dependencies=guarded)
    def promote_apply(payload: dict = Body(default={})) -> dict:  # noqa: B008
        dev, prod = pair()
        keys = keys_of(payload)
        return write(prod, "promote", ", ".join(keys), _str(payload, "confirm"),
                     lambda: ed.plan_promote(dev.root, prod.root, keys), _dict(payload, "hashes"))

    @app.get("/api/audit", dependencies=guarded)
    def audit_view(instance: str | None = None, limit: int = Query(default=200, ge=1, le=1000)) -> dict:
        if instance is not None:
            instance_or_404(instance)
        return {"entries": audit.read(limit, instance)}

    @app.get("/api/alerts", dependencies=guarded)
    def alerts_view() -> dict:
        status = alerts.status() if alerts is not None else {"on": False, "why": cfg.alerts.off_reason or
                                                             "alerts are off", "instances": list(cfg.alerts.instances),
                                                             "failing": [], "last_sent": {}}
        return status | {"deploy_busy": gd.agent_busy(cfg.agent_lock)}

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

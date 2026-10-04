"""The server API v1 (v2.2, LUMI-212) — the routes, the token rule, the one-at-a-time lock.

Every ``/v1/*`` route except ``health`` requires ``Authorization: Bearer <token>`` (constant-time compare;
the token is never logged or echoed). One core, one session: a turn, a command and a session switch hold
the same lock, so a second request while one runs gets ``409 busy`` instead of interleaving. Blocking turns
only (streaming arrives in v2.3); the emotion contract is validated by the core as always.
"""

from __future__ import annotations

import logging
import secrets
import threading
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from core.commands import run_command
from core.llm import LLMError

log = logging.getLogger("lumi.server")


class TurnRequest(BaseModel):
    text: str
    images: list[dict[str, Any]] | None = None  # v0.22 image blocks (base64) — /image over the API


class CommandRequest(BaseModel):
    line: str
    confirmed: bool = False


def stats_dict(stats: Any) -> dict[str, Any] | None:
    """A model call's ``ResponseStats`` as JSON (``None`` before any call)."""
    if stats is None:
        return None
    return {
        "model": stats.model, "latency_ms": stats.latency_ms,
        "input_tokens": stats.input_tokens, "output_tokens": stats.output_tokens,
        "cache_read_tokens": stats.cache_read_tokens, "cache_write_tokens": stats.cache_write_tokens,
        "thinking": stats.thinking,
    }


def totals_dict(totals: Any) -> dict[str, int]:
    return {
        "turns": totals.turns, "input_tokens": totals.input_tokens, "output_tokens": totals.output_tokens,
        "cache_read_tokens": totals.cache_read_tokens, "cache_write_tokens": totals.cache_write_tokens,
        "latency_ms": totals.latency_ms,
    }


def state_snapshot(core: Any, session: Any, *, env: str | None, version: str | None) -> dict[str, Any]:
    """Everything a client's status/stats lines read — one fetch per turn/command, not one per render."""
    emo = getattr(core, "last_emotion", None)
    return {
        "env": env, "version": version, "session_id": getattr(session, "id", None),
        "model": core.model, "provider": core.provider, "profile": core.profile,
        "thinking": core.thinking, "style": core.style,
        "last_emotion": {"emotion": emo.emotion.value, "intensity": emo.intensity} if emo else None,
        "last_intent": getattr(core, "last_intent", None),
        "last_thinking": getattr(core, "last_thinking", None),
        "last_ttft_ms": getattr(core, "last_ttft_ms", None),
        "last_stats": stats_dict(core.last_stats),
        "totals": totals_dict(core.totals),
        "mood": core.mood, "theme": core.theme,
        "think_show": getattr(core, "think_show", "debug"),
    }


class _Service:
    """The one core + its current session, behind one lock."""

    def __init__(self, core: Any, session: Any, *, env: str | None, version: str | None) -> None:
        self.core = core
        self.session = session
        self.env = env
        self.version = version
        self.lock = threading.Lock()

    def state(self) -> dict[str, Any]:
        return state_snapshot(self.core, self.session, env=self.env, version=self.version)

    def acquire(self) -> None:
        if not self.lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="busy")


def create_app(core: Any, *, token: str, session: Any = None, env: str | None = None,
               version: str | None = None, on_new_session: Callable[[], None] | None = None) -> FastAPI:
    """The v1 app around an injected core (a mock model in tests). ``token`` is required.

    ``on_new_session`` runs in the background after ``/v1/session/new`` (the ambient re-snapshot, as the
    TUI's ``/new`` always did) — best-effort, never delaying the response."""
    if not token:
        raise ValueError("LUMI_SERVER_TOKEN is required to serve")
    service = _Service(core, session if session is not None else core.start_session(), env=env, version=version)
    expected = f"Bearer {token}".encode()
    # No interactive docs / schema endpoints: the API is private to its own clients.
    app = FastAPI(title="Lumi", version=version or "dev", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service = service

    def require_token(authorization: str | None = Header(default=None)) -> None:
        if not authorization or not secrets.compare_digest(authorization.encode(), expected):
            raise HTTPException(status_code=401, detail="unauthorized")

    guarded = [Depends(require_token)]

    @app.get("/v1/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "env": env, "version": version}

    @app.get("/v1/state", dependencies=guarded)
    def state() -> dict[str, Any]:
        return service.state()

    @app.post("/v1/turn", dependencies=guarded)
    def turn(req: TurnRequest) -> dict[str, Any]:
        service.acquire()
        try:
            result = service.core.reply(req.text, service.session, images=req.images)
        except LLMError as exc:  # the model failed — a readable error, the session stays usable
            log.warning("turn failed: %s", exc)
            raise HTTPException(status_code=502, detail=f"model unavailable: {exc}") from None
        except Exception:  # anything else — logged in full, the client gets a readable line (the TUI rule)
            log.exception("turn failed")
            raise HTTPException(status_code=500, detail="turn failed — see the server log") from None
        finally:
            service.lock.release()
        core = service.core
        return {
            "reply": result.reply, "emotion": result.emotion.value, "intensity": result.intensity,
            "thinking": getattr(core, "last_thinking", None), "intent": getattr(core, "last_intent", None),
            "style": core.style, "stats": stats_dict(core.last_stats), "state": service.state(),
        }

    @app.post("/v1/command", dependencies=guarded)
    def command(req: CommandRequest) -> dict[str, Any]:
        service.acquire()
        try:
            result = run_command(service.core, service.session, req.line, confirmed=req.confirmed)
        finally:
            service.lock.release()
        if result is None:
            return {"handled": False, "result": None, "state": service.state()}
        return {"handled": True, "result": asdict(result), "state": service.state()}

    @app.post("/v1/session/new", dependencies=guarded)
    def new_session() -> dict[str, Any]:
        service.acquire()
        try:
            try:
                service.core.end_session(service.session)  # its summary + facts, best-effort
            except Exception:  # noqa: BLE001 — never block a new session on housekeeping
                log.warning("ending the previous session failed", exc_info=True)
            service.session = service.core.start_session()
        finally:
            service.lock.release()
        if on_new_session is not None:
            threading.Thread(target=on_new_session, daemon=True, name="lumi-new-session").start()
        return {"ok": True, "state": service.state()}

    return app

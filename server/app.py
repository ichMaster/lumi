"""The server API v1 (v2.2 LUMI-212, v2.3 LUMI-216) — the routes, the token rule, the one-at-a-time lock.

Every ``/v1/*`` route except ``health`` requires ``Authorization: Bearer <token>`` (constant-time compare;
the token is never logged or echoed). One core, one session: a turn, a command and a session switch hold
the same lock, so a second request while one runs gets ``409 busy`` instead of interleaving.

v2.3: a turn can **stream** (``POST /v1/turn/stream`` → Server-Sent Events ``delta``/``think``/``done``/
``error``) and every turn carries an optional **idempotent** ``turn_id``: each turn runs in its own runner
thread that holds the lock to the end, results are kept by id, and a known id waits for / returns the stored
result instead of running again — so a client retrying after a dropped stream never makes Лілі answer
twice. The emotion contract is validated by the core on completion, as always (``done`` carries it).
"""

from __future__ import annotations

import json
import logging
import queue
import secrets
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterator
from dataclasses import asdict
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.commands import run_command
from core.llm import LLMError

log = logging.getLogger("lumi.server")


class TurnRequest(BaseModel):
    text: str
    images: list[dict[str, Any]] | None = None  # v0.22 image blocks (base64) — /image over the API
    turn_id: str | None = None  # v2.3: idempotency key — a known id returns the stored result, never reruns


MAX_KEPT_TURNS = 32  # finished turns remembered by id (for retries after a dropped stream)
_END = ("_end", None)


def sse(event: str, data: dict[str, Any]) -> str:
    """One Server-Sent Event — ``data`` as one JSON line (newline-safe)."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class _TurnRecord:
    """One turn's lifecycle: streamed events while it runs, then its result (or error), kept by id."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.events: queue.Queue = queue.Queue()
        self.result: dict[str, Any] | None = None
        self.error: tuple[int, str] | None = None


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
        "stream_enabled": bool(getattr(core, "stream_enabled", False)),  # v2.3: the client streams when on
    }


class _Service:
    """The one core + its current session, behind one lock."""

    def __init__(self, core: Any, session: Any, *, env: str | None, version: str | None) -> None:
        self.core = core
        self.session = session
        self.env = env
        self.version = version
        self.lock = threading.Lock()
        self._turns: OrderedDict[str, _TurnRecord] = OrderedDict()  # v2.3: turn_id → record
        self._turns_lock = threading.Lock()

    def state(self) -> dict[str, Any]:
        return state_snapshot(self.core, self.session, env=self.env, version=self.version)

    def acquire(self) -> None:
        if not self.lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="busy")

    # --- v2.3 turns: one runner thread each, kept by id -------------------------------------------------
    def begin_turn(self, req: TurnRequest, *, stream: bool) -> tuple[_TurnRecord, bool]:
        """The record for this turn and whether it is NEW. A known ``turn_id`` returns the existing record
        (running or done) without running anything; a new turn takes the lock (409 if another runs)."""
        with self._turns_lock:
            if req.turn_id and req.turn_id in self._turns:
                return self._turns[req.turn_id], False
            self.acquire()  # 409 while a different turn / command runs
            record = _TurnRecord()
            if req.turn_id:
                self._turns[req.turn_id] = record
                self._forget_old_turns()
        threading.Thread(target=self._run_turn, args=(record, req, stream), daemon=True,
                         name="lumi-turn").start()
        return record, True

    def _forget_old_turns(self) -> None:
        while len(self._turns) > MAX_KEPT_TURNS:
            oldest_id, oldest = next(iter(self._turns.items()))
            if not oldest.done.is_set():
                break  # never forget a running turn
            del self._turns[oldest_id]

    def _run_turn(self, record: _TurnRecord, req: TurnRequest, stream: bool) -> None:
        """The runner: owns the lock for the whole turn — a disconnecting client can't stop it midway."""
        try:
            hooks: dict[str, Any] = {}
            if stream:
                hooks = {
                    "on_delta": lambda text: record.events.put(("delta", {"text": text})),
                    "on_think_delta": lambda text: record.events.put(("think", {"text": text})),
                }
            result = self.core.reply(req.text, self.session, images=req.images, **hooks)
            record.result = self.turn_payload(result)
        except LLMError as exc:  # the model failed — a readable error, the session stays usable
            log.warning("turn failed: %s", exc)
            record.error = (502, f"model unavailable: {exc}")
        except Exception:  # anything else — logged in full, the client gets a readable line (the TUI rule)
            log.exception("turn failed")
            record.error = (500, "turn failed — see the server log")
        finally:
            self.lock.release()
            record.done.set()
            record.events.put(_END)

    def turn_payload(self, result: Any) -> dict[str, Any]:
        core = self.core
        return {
            "reply": result.reply, "emotion": result.emotion.value, "intensity": result.intensity,
            "thinking": getattr(core, "last_thinking", None), "intent": getattr(core, "last_intent", None),
            "style": core.style, "stats": stats_dict(core.last_stats), "state": self.state(),
        }


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
        """A blocking turn — or, for a known ``turn_id``, the stored result of that turn (never a rerun)."""
        record, _ = service.begin_turn(req, stream=False)
        record.done.wait()
        if record.error is not None:
            raise HTTPException(status_code=record.error[0], detail=record.error[1])
        return record.result

    @app.post("/v1/turn/stream", dependencies=guarded)
    def turn_stream(req: TurnRequest) -> StreamingResponse:
        """A streamed turn as Server-Sent Events: ``delta``/``think`` while she answers, then exactly one
        ``done`` (the blocking payload) or ``error``. A known ``turn_id`` skips straight to its outcome."""
        record, fresh = service.begin_turn(req, stream=True)

        def events() -> Iterator[str]:
            if fresh:
                while True:
                    kind, data = record.events.get()
                    if kind == "_end":
                        break
                    yield sse(kind, data)
            record.done.wait()
            if record.error is not None:
                yield sse("error", {"detail": record.error[1]})
            else:
                yield sse("done", record.result)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

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

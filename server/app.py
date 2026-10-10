"""The server API v1 (v2.2 LUMI-212, v2.3 LUMI-216) — the routes, the token rule, the one-at-a-time lock.

Every ``/v1/*`` route except ``health`` requires ``Authorization: Bearer <token>`` (constant-time compare;
the token is never logged or echoed). One core, one session: a turn, a command and a session switch hold
the same lock, so a second request while one runs gets ``409 busy`` instead of interleaving.

v2.3: a turn can **stream** (``POST /v1/turn/stream`` → Server-Sent Events ``delta``/``think``/``done``/
``error``) and every turn carries an optional **idempotent** ``turn_id``: each turn runs in its own runner
thread that holds the lock to the end, results are kept by id, and a known id waits for / returns the stored
result instead of running again — so a client retrying after a dropped stream never makes Лілі answer
twice. The emotion contract is validated by the core on completion, as always (``done`` carries it).

v2.4: the server **speaks first** — ``GET /v1/events`` is a long-lived SSE stream every client listens to:
the current ``state`` first, then a ``turn`` event ``{emotion, intensity, theme}`` after every turn (the
face signal over the wire — a theme *name*, never a face file) and ``state`` whenever the snapshot changes.
The snapshot is taken by whoever holds the lock (a turn's runner, a command, a session switch), so it is
never torn mid-turn. A request may name its client (``X-Lumi-Client``); the events it causes carry that id
as ``origin`` (``null`` for the server's own), so a client can tell its own echoes from the rest.
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
EVENT_QUEUE_MAX = 256  # per listener; a full queue drops its oldest event — a slow client never stalls a turn
HEARTBEAT_S = 15.0  # a ':' comment after this much silence keeps the stream honest (and cancellable)
ORIGIN_MAX = 64  # an X-Lumi-Client id longer than this is cut
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def sse(event: str, data: dict[str, Any]) -> str:
    """One Server-Sent Event — ``data`` as one JSON line (newline-safe)."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def origin_of(header: str | None) -> str | None:
    """The requesting client's id (``X-Lumi-Client``) as events carry it — ``None`` when not given."""
    return (header or "").strip()[:ORIGIN_MAX] or None


def _offer(sub: queue.Queue, item: Any) -> None:
    """Put without ever blocking: a full queue loses its oldest item first."""
    while True:
        try:
            sub.put_nowait(item)
            return
        except queue.Full:
            try:
                sub.get_nowait()
            except queue.Empty:
                pass


class EventBus:
    """v2.4: fan-out of the server's events to every ``/v1/events`` listener — one bounded queue each.

    ``publish`` never blocks (a full queue drops its oldest event); ``close`` ends every stream — the
    server's shutdown, so a listening client never holds a graceful stop open."""

    def __init__(self, maxsize: int = EVENT_QUEUE_MAX) -> None:
        self._maxsize = maxsize
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._closed = False

    def subscribe(self) -> queue.Queue:
        sub: queue.Queue = queue.Queue(maxsize=self._maxsize)
        with self._lock:
            if self._closed:
                _offer(sub, _END)  # a listener arriving during shutdown ends at once
            else:
                self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: queue.Queue) -> None:
        with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)

    @property
    def listeners(self) -> int:
        with self._lock:
            return len(self._subs)

    def publish(self, event: str, data: dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subs)
        for sub in subs:
            _offer(sub, (event, data))

    def close(self) -> None:
        with self._lock:
            self._closed = True
            subs, self._subs = self._subs, []
        for sub in subs:
            _offer(sub, _END)


def event_frames(sub: queue.Queue, first: tuple[str, dict[str, Any]], *,
                 heartbeat_s: float | None = None) -> Iterator[str]:
    """The ``/v1/events`` body: ``first`` (the current state), then each published event as it comes, a
    ``:`` heartbeat comment whenever ``heartbeat_s`` (default ``HEARTBEAT_S``) passes in silence; ends when
    the bus closes."""
    wait = HEARTBEAT_S if heartbeat_s is None else heartbeat_s
    yield sse(*first)
    while True:
        try:
            item = sub.get(timeout=wait)
        except queue.Empty:
            yield ": heartbeat\n\n"
            continue
        if item is _END:
            return
        yield sse(*item)


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
    level, name = core.closeness_status()  # v2.4: the level by name only — the raw value stays internal
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
        "closeness": {"level": level, "name": name},  # v2.4
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
        self.bus = EventBus()  # v2.4: the push channel
        self.last_state = self.state()  # the last consistent snapshot — what a new listener starts from

    def state(self) -> dict[str, Any]:
        return state_snapshot(self.core, self.session, env=self.env, version=self.version)

    def acquire(self) -> None:
        if not self.lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="busy")

    # --- v2.4 pushes ------------------------------------------------------------------------------------
    def publish_state(self, origin: str | None = None) -> dict[str, Any]:
        """Snapshot the state and push it to every listener if it changed. The caller holds the lock, so
        the snapshot is never taken mid-turn (v2.2 review #7). Returns the snapshot."""
        snap = self.state()
        changed, self.last_state = snap != self.last_state, snap
        if changed:
            self.bus.publish("state", {"origin": origin, "state": snap})
        return snap

    def refresh_state(self) -> None:
        """Re-snapshot from outside a request (the mood computed at start) — after any turn in flight."""
        with self.lock:
            self.publish_state()

    def _publish_state_quietly(self, origin: str | None) -> None:
        try:
            self.publish_state(origin)
        except Exception:  # noqa: BLE001 — a failed push never keeps the lock or kills the runner
            log.warning("publishing the state failed", exc_info=True)

    # --- v2.3 turns: one runner thread each, kept by id -------------------------------------------------
    def begin_turn(self, req: TurnRequest, *, stream: bool,
                   origin: str | None = None) -> tuple[_TurnRecord, bool]:
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
        threading.Thread(target=self._run_turn, args=(record, req, stream, origin), daemon=True,
                         name="lumi-turn").start()
        return record, True

    def _forget_old_turns(self) -> None:
        while len(self._turns) > MAX_KEPT_TURNS:
            oldest_id, oldest = next(iter(self._turns.items()))
            if not oldest.done.is_set():
                break  # never forget a running turn
            del self._turns[oldest_id]

    def _run_turn(self, record: _TurnRecord, req: TurnRequest, stream: bool, origin: str | None) -> None:
        """The runner: owns the lock for the whole turn — a disconnecting client can't stop it midway.
        Before letting go it pushes the turn's face state and the new snapshot (v2.4)."""
        try:
            hooks: dict[str, Any] = {}
            if stream:
                hooks = {
                    "on_delta": lambda text: record.events.put(("delta", {"text": text})),
                    "on_think_delta": lambda text: record.events.put(("think", {"text": text})),
                }
            result = self.core.reply(req.text, self.session, images=req.images, **hooks)
            self.bus.publish("turn", {"emotion": result.emotion.value, "intensity": result.intensity,
                                      "theme": self.core.theme, "origin": origin})
            record.result = self.turn_payload(result, origin)
        except LLMError as exc:  # the model failed — a readable error, the session stays usable
            log.warning("turn failed: %s", exc)
            record.error = (502, f"model unavailable: {exc}")
        except Exception:  # anything else — logged in full, the client gets a readable line (the TUI rule)
            log.exception("turn failed")
            record.error = (500, "turn failed — see the server log")
        finally:
            if record.result is None:
                self._publish_state_quietly(origin)  # a failed turn may still have moved the state
            self.lock.release()
            record.done.set()
            record.events.put(_END)

    def turn_payload(self, result: Any, origin: str | None = None) -> dict[str, Any]:
        """The turn's outcome; its ``state`` is the snapshot just pushed (taken under the runner's lock)."""
        core = self.core
        return {
            "reply": result.reply, "emotion": result.emotion.value, "intensity": result.intensity,
            "thinking": getattr(core, "last_thinking", None), "intent": getattr(core, "last_intent", None),
            "style": core.style, "stats": stats_dict(core.last_stats), "state": self.publish_state(origin),
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
        """The current state — fresh when idle; while a turn runs, the last consistent snapshot."""
        if not service.lock.acquire(blocking=False):
            return service.last_state
        try:
            return service.publish_state()
        finally:
            service.lock.release()

    @app.get("/v1/events", dependencies=guarded)
    def events() -> StreamingResponse:
        """v2.4: the push channel (SSE) — the current ``state`` first, then ``turn`` / ``state`` as they
        happen, ``:`` heartbeats in between; it ends only when the client leaves or the server stops."""

        def body() -> Iterator[str]:
            sub = service.bus.subscribe()  # before reading the state: nothing published in between is lost
            try:
                yield from event_frames(sub, ("state", {"origin": None, "state": service.last_state}))
            finally:
                service.bus.unsubscribe(sub)

        return StreamingResponse(body(), media_type="text/event-stream", headers=_SSE_HEADERS)

    @app.post("/v1/turn", dependencies=guarded)
    def turn(req: TurnRequest, x_lumi_client: str | None = Header(default=None)) -> dict[str, Any]:
        """A blocking turn — or, for a known ``turn_id``, the stored result of that turn (never a rerun)."""
        record, _ = service.begin_turn(req, stream=False, origin=origin_of(x_lumi_client))
        record.done.wait()
        if record.error is not None:
            raise HTTPException(status_code=record.error[0], detail=record.error[1])
        return record.result

    @app.post("/v1/turn/stream", dependencies=guarded)
    def turn_stream(req: TurnRequest, x_lumi_client: str | None = Header(default=None)) -> StreamingResponse:
        """A streamed turn as Server-Sent Events: ``delta``/``think`` while she answers, then exactly one
        ``done`` (the blocking payload) or ``error``. A known ``turn_id`` skips straight to its outcome."""
        record, fresh = service.begin_turn(req, stream=True, origin=origin_of(x_lumi_client))

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

        return StreamingResponse(events(), media_type="text/event-stream", headers=_SSE_HEADERS)

    @app.post("/v1/command", dependencies=guarded)
    def command(req: CommandRequest, x_lumi_client: str | None = Header(default=None)) -> dict[str, Any]:
        service.acquire()
        try:
            result = run_command(service.core, service.session, req.line, confirmed=req.confirmed)
            snap = service.publish_state(origin_of(x_lumi_client))  # e.g. /model-set, /style, /theme
        finally:
            service.lock.release()
        if result is None:
            return {"handled": False, "result": None, "state": snap}
        return {"handled": True, "result": asdict(result), "state": snap}

    @app.post("/v1/session/new", dependencies=guarded)
    def new_session(x_lumi_client: str | None = Header(default=None)) -> dict[str, Any]:
        service.acquire()
        try:
            try:
                service.core.end_session(service.session)  # its summary + facts, best-effort
            except Exception:  # noqa: BLE001 — never block a new session on housekeeping
                log.warning("ending the previous session failed", exc_info=True)
            service.session = service.core.start_session()
            snap = service.publish_state(origin_of(x_lumi_client))
        finally:
            service.lock.release()
        if on_new_session is not None:
            threading.Thread(target=on_new_session, daemon=True, name="lumi-new-session").start()
        return {"ok": True, "state": snap}

    return app

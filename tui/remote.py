"""`RemoteCore` — the TUI's core surface over the v2.2 server API (LUMI-213, client mode).

With ``LUMI_SERVER=on`` the TUI builds no core: this object stands in for it, implementing exactly what
the TUI uses outside the command layer (the members ``tests/contract/test_remote_core_surface.py``
pins). Turns, commands and session switches go over HTTP; the status/stats lines read a **state
snapshot** refreshed after every call (never a request per render). Blocking only — streaming arrives in
v2.3. What the server can't host yet raises :class:`NotInServerMode` (the TUI turns it into a line).
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

import httpx

from core.agent import UsageTotals
from core.clock import system_clock
from core.commands import CommandResult
from core.emotion import Emotion, EmotionState
from core.llm import LLMError, ResponseStats

NOT_YET = "not yet in server mode"
log = logging.getLogger("lumi.client")


class MalformedStream(ValueError):
    """An SSE frame whose data isn't the JSON the server sends."""


class _StreamBroken(Exception):
    """The stream failed before its outcome — the turn is fetched by its id the blocking way."""


def parse_sse(lines: Iterable[str]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Server-Sent Events → ``(event, data)`` frames (v2.3). ``:`` comments and unknown fields are ignored,
    multi-line ``data`` is joined, a blank line ends a frame; data that isn't JSON → :class:`MalformedStream`."""
    event: str | None = None
    data: list[str] = []

    def frame() -> tuple[str, dict[str, Any]]:
        try:
            payload = json.loads("\n".join(data)) if data else {}
        except ValueError:
            raise MalformedStream(f"bad data in a {event or 'message'!r} frame") from None
        if not isinstance(payload, dict):
            raise MalformedStream(f"non-object data in a {event or 'message'!r} frame")
        return event or "message", payload

    for raw in lines:
        line = raw.rstrip("\r")
        if not line:
            if event is not None or data:
                yield frame()
            event, data = None, []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            event = value
        elif field == "data":
            data.append(value)
    if event is not None or data:  # a last frame without its blank line
        yield frame()


class ServerUnavailable(LLMError):
    """The server can't be reached (down / wrong address) or rejected the token."""


class ServerAuthError(ServerUnavailable):
    """The server is up but rejected the client token."""


class ServerBusy(LLMError):
    """The server is running another request (one at a time) — try again."""


class NotInServerMode(RuntimeError):
    """A feature the v2.2 server doesn't host yet (the thought-stream, the scheduler — v2.5)."""


# What a client can't host in v2.2 — (config field, its off value, the name the client shows). The server
# takes these over in v2.5 (the brain moves); until then a client turns them off and says so once.
_CLIENT_OFF: tuple[tuple[str, Any, str], ...] = (
    ("bridge", False, "Telegram bridge"),
    ("scheduler", False, "scheduler"),
    ("thoughts", False, "thought-stream"),
    ("idle_nudge", False, "idle nudges"),
    ("voice", False, "voicer"),
    ("dictation", False, "dictation"),
    ("mode_set", "text", "voice mode"),
)


def client_mode_config(cfg: Any) -> tuple[Any, list[str]]:
    """The client's config with the not-yet-hosted features off + the names of those this .env had on."""
    was_on = [name for field, off, name in _CLIENT_OFF if getattr(cfg, field) != off]
    return replace(cfg, **{field: off for field, off, _ in _CLIENT_OFF}), was_on


@dataclass(frozen=True)
class RemoteSession:
    """A handle to the server's current session (the server owns the real one)."""

    id: str | None


def _emotion(d: dict | None) -> EmotionState | None:
    if not d:
        return None
    return EmotionState(reply="", emotion=Emotion(d["emotion"]), intensity=float(d["intensity"]))


def _stats(d: dict | None) -> ResponseStats | None:
    return ResponseStats(**d) if d else None


def _totals(d: dict | None) -> UsageTotals:
    return UsageTotals(**d) if d else UsageTotals()


class RemoteCore:
    """The client-mode stand-in for :class:`core.agent.Core` — see the module doc."""

    is_remote = True
    last_compaction = None
    last_tool_calls: list = []

    def __init__(self, base_url: str, token: str, *, client: httpx.Client | None = None,
                 clock: Callable[[], datetime] | None = None, timeout_s: float = 600.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = client or httpx.Client(
            base_url=self.base_url, timeout=httpx.Timeout(timeout_s, connect=3.0),
        )
        self._auth = {"Authorization": f"Bearer {token}"}
        self.clock = clock or system_clock
        self._state: dict[str, Any] = {}
        self.last_thinking: str | None = None
        self.last_intent: str | None = None

    # --- transport -----------------------------------------------------------------------------------
    def _call(self, method: str, path: str, *, auth: bool = True, **kw: Any) -> dict[str, Any]:
        try:
            res = self._http.request(method, path, headers=self._auth if auth else None, **kw)
        except httpx.TransportError as exc:  # refused / timed out / dropped
            raise ServerUnavailable(f"server unreachable at {self.base_url} ({type(exc).__name__})") from None
        if res.status_code == 401:
            raise ServerAuthError("the server rejected the token — check LUMI_SERVER_TOKEN")
        if res.status_code == 409:
            raise ServerBusy("Лілі is busy with another request — try again")
        if res.status_code >= 400:
            try:
                detail = res.json().get("detail")
            except ValueError:
                detail = None
            raise LLMError(detail or f"server error {res.status_code}")
        try:
            return res.json()
        except ValueError:  # something else listens on the port (a web server, a proxy page)
            raise ServerUnavailable(
                f"{self.base_url} answered, but not as a Lumi server (HTTP {res.status_code})"
            ) from None

    def _absorb(self, payload: dict[str, Any]) -> None:
        state = payload.get("state")
        if isinstance(state, dict):
            self._state = state
            self.last_thinking = state.get("last_thinking")
            self.last_intent = state.get("last_intent")

    def connect(self) -> None:
        """Reach the server and prove the token — readable :class:`ServerUnavailable` otherwise."""
        self._call("GET", "/v1/health", auth=False)
        self._state = self._call("GET", "/v1/state")

    def refresh(self) -> None:
        self._state = self._call("GET", "/v1/state")

    def health(self) -> dict[str, Any]:
        """The open ``/v1/health`` (env + version) — no token needed."""
        return self._call("GET", "/v1/health", auth=False)

    @property
    def session_id(self) -> str | None:
        return self._state.get("session_id")

    # --- the turn + commands -------------------------------------------------------------------------
    def reply(self, user_text: str, session: Any, *, images: list[dict] | None = None,
              on_delta: Callable[[str], None] | None = None,
              on_think_delta: Callable[[str], None] | None = None,
              register: str | None = None) -> EmotionState:
        """One turn on the server — streamed into the TUI's v1.4 callbacks when the server streams (v2.3),
        else blocking. Every turn carries a fresh ``turn_id``: if the stream breaks, the same id is asked
        for the blocking way and the server returns that turn's outcome — she never answers twice."""
        body: dict[str, Any] = {"text": user_text, "turn_id": uuid.uuid4().hex}
        if images:
            body["images"] = images
        res: dict[str, Any] | None = None
        if on_delta is not None and self.stream_enabled:
            try:
                res = self._stream_turn(body, on_delta, on_think_delta)
            except _StreamBroken as exc:
                log.info("stream broke (%s) — fetching turn %s the blocking way", exc, body["turn_id"])
        if res is None:
            res = self._call("POST", "/v1/turn", json=body)
        self._absorb(res)
        self.last_thinking = res.get("thinking")
        self.last_intent = res.get("intent")
        return EmotionState(reply=res["reply"], emotion=Emotion(res["emotion"]), intensity=float(res["intensity"]))

    def _stream_turn(self, body: dict[str, Any], on_delta: Callable[[str], None],
                     on_think_delta: Callable[[str], None] | None) -> dict[str, Any]:
        """Consume ``/v1/turn/stream``: deltas → the callbacks, ``done`` → the payload. Anything short of an
        outcome (a drop, a garbled frame, an early end, an older server) → :class:`_StreamBroken`."""
        try:
            with self._http.stream("POST", "/v1/turn/stream", json=body, headers=self._auth) as res:
                if res.status_code == 401:
                    raise ServerAuthError("the server rejected the token — check LUMI_SERVER_TOKEN")
                if res.status_code == 409:
                    raise ServerBusy("Лілі is busy with another request — try again")
                if res.status_code == 404:
                    raise _StreamBroken("no stream route (an older server)")
                if res.status_code >= 400:
                    raise _StreamBroken(f"HTTP {res.status_code}")
                for event, data in parse_sse(res.iter_lines()):
                    if event == "delta":
                        on_delta(str(data.get("text", "")))
                    elif event == "think" and on_think_delta is not None:
                        on_think_delta(str(data.get("text", "")))
                    elif event == "done":
                        return data
                    elif event == "error":
                        raise LLMError(str(data.get("detail") or "turn failed"))
        except httpx.TransportError as exc:
            raise _StreamBroken(type(exc).__name__) from None
        except MalformedStream as exc:
            raise _StreamBroken(str(exc)) from None
        raise _StreamBroken("the stream ended without an outcome")

    def command(self, line: str, *, confirmed: bool = False) -> CommandResult | None:
        """A slash command through the server's command layer (``None`` → not a layer command)."""
        res = self._call("POST", "/v1/command", json={"line": line, "confirmed": confirmed})
        self._absorb(res)
        if not res.get("handled"):
            return None
        return CommandResult(**res["result"])

    # --- the session (the server owns it) ------------------------------------------------------------
    def start_session(self) -> RemoteSession:
        """A handle to the server's CURRENT session (no side effect)."""
        self.refresh()
        return RemoteSession(id=self._state.get("session_id"))

    def end_session(self, session: Any) -> None:
        """Close this sitting on the server (summary) — it starts the next session, like `/new` and quit."""
        self._absorb(self._call("POST", "/v1/session/new"))

    # --- what the server does on its own --------------------------------------------------------------
    def ensure_mood(self) -> None:  # the server computes today's mood at its start
        return None

    def ensure_backfill(self) -> None:  # the server indexes the recall store at its start
        return None

    def set_world_context(self, world: Any) -> None:  # the server takes its own ambient snapshot
        return None

    # --- not yet (v2.5 moves the thought-stream + scheduler into the server) -------------------------
    def tick_think(self, *args: Any, **kwargs: Any) -> Any:
        raise NotInServerMode(f"thoughts: {NOT_YET}")

    def run_directive(self, *args: Any, **kwargs: Any) -> Any:
        raise NotInServerMode(f"%directives: {NOT_YET}")

    # --- the status/stats lines (from the snapshot) --------------------------------------------------
    @property
    def model(self) -> str:
        return self._state.get("model") or "?"

    @property
    def provider(self) -> str | None:
        return self._state.get("provider")

    @property
    def profile(self) -> str | None:
        return self._state.get("profile")

    @property
    def thinking(self) -> bool:
        return bool(self._state.get("thinking"))

    @property
    def style(self) -> str:
        return self._state.get("style") or "normal"

    @property
    def mood(self) -> str | None:
        return self._state.get("mood")

    @property
    def theme(self) -> str | None:
        return self._state.get("theme")

    @property
    def stream_enabled(self) -> bool:
        """v2.3: stream when the server's core does (``LUMI_STREAM`` on the server) — the TUI branches on it."""
        return bool(self._state.get("stream_enabled"))

    @property
    def think_show(self) -> str:
        return self._state.get("think_show") or "debug"

    @property
    def last_ttft_ms(self) -> int | None:
        return self._state.get("last_ttft_ms")

    @property
    def last_emotion(self) -> EmotionState | None:
        return _emotion(self._state.get("last_emotion"))

    @property
    def last_stats(self) -> ResponseStats | None:
        return _stats(self._state.get("last_stats"))

    @property
    def totals(self) -> UsageTotals:
        return _totals(self._state.get("totals"))

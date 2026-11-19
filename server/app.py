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
as ``origin`` (``null`` for the server's own), so a client can tell its own echoes from the rest. Typed
``%directives`` run here too (``POST /v1/directive``) and an open thought is pushed as ``thought``.
"""

from __future__ import annotations

import json
import logging
import queue
import secrets
import threading
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from core.actions import (
    UPLOAD_MAX_PICTURE,
    ActionLog,
    Conflict,
    rename,
    restore,
    trash,
    upload,
)
from core.agent import tool_calls_view
from core.browse import (
    BadPath,
    BrowseError,
    NotFound,
    NotServed,
    TooLarge,
    list_folder,
    read_picture,
    read_text,
)
from core.browse import _resolve as browse_resolve
from core.commands import run_command
from core.gallery import GALLERY_MAX_LIMIT, list_pictures, thumbnail
from core.images import image_block
from core.journal import list_days, read_day
from core.llm import LLMError
from core.thoughts import THOUGHT_PAGE_MAX
from core.user import may_read_journal
from server.voice import (
    AUTO,
    VOICE_ID_RE,
    LineVoicer,
    VoiceSetup,
    VoiceStore,
    VoiceSwitch,
    VoiceUnavailable,
)
from state.bus import mirror_reply, mirror_thought, mirror_user

log = logging.getLogger("lumi.server")


class TurnRequest(BaseModel):
    text: str
    images: list[dict[str, Any]] | None = None  # v0.22 image blocks (base64) — /image over the API
    sandbox_images: list[str] | None = None  # v4.5: pictures of her sandbox by path — the server reads them
    turn_id: str | None = None  # v2.3: idempotency key — a known id returns the stored result, never reruns
    # v3.1: whose conversation — "owner" (default: today's path, byte for byte) or "agora" (the room's own path)
    channel: Literal["owner", "agora"] = "owner"
    speaker: str | None = None  # v3.1: who said the room line she answers (the agora channel only)
    # v3.1: the room's register ("voice"); ignored on his channel. Named register_ in Python (pydantic's BaseModel
    # has a ``register``), "register" on the wire.
    register_: str | None = Field(default=None, alias="register")

    model_config = ConfigDict(populate_by_name=True)


class VoiceRequest(BaseModel):
    """v4.6: the «Voice» switch — on or off for the window and Telegram both."""
    enabled: bool


VOICE_HEADERS = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=86400"}
VOICE_NOT_SERVED = "voice is not served"


class PathRequest(BaseModel):
    """v4.5: one sandbox path (trash, restore)."""
    path: str


class RenameRequest(BaseModel):
    """v4.5: a sandbox path and its new name (rename)."""
    path: str
    name: str


class ObservedLineIn(BaseModel):
    """v3.1: one room line the bridge forwards (agora space API v1)."""

    id: str
    speaker: str
    text: str
    ts: str


class ObserveRequest(BaseModel):
    channel: Literal["agora"]
    lines: list[ObservedLineIn]


class InitiativePostedRequest(BaseModel):
    """v3.4: the bridge posted her offer — the posted line's Matrix ``event_id`` (agora space API v2)."""

    event_id: str


AGORA_API = 2  # the agora space API version this server serves (health's "agora"): v3.4 — v1 + her initiative
OBSERVE_BATCH_MAX = 200  # the contract's limits.observe_batch_max
ROOM_GRACE_S = 2.0  # the server's backstop waits this much past the room's own budget before a 504
ROOM_ONLY = "the room's token reaches only the agora space"
# v4.1 LUMI-302: the window's read routes over her sandbox — a refusal's status, and the headers each answer carries
BROWSE_STATUS: tuple[tuple[type[BrowseError], int], ...] = ((BadPath, 400), (NotFound, 404), (TooLarge, 413),
                                                           (NotServed, 415), (Conflict, 409))  # v4.5: + 409
FILES_JSON_HEADERS = {"Cache-Control": "no-store"}
SANDBOX_IMAGES_MAX = 4  # v4.5: pictures a turn may show her by path
DOWNLOAD_MAX = 50 * 1024 * 1024  # v4.5: a file the window may save to the Mac
FILES_RAW_HEADERS = {"X-Content-Type-Options": "nosniff", "Content-Disposition": "inline",
                     "Cache-Control": "private, max-age=300"}
ROOM_BUSY = "Лілі is answering the room — try again"
JOURNAL_OWNER_ONLY = "the journal is the owner's"  # v4.3: owner/admin-only by rule (ROADMAP §v9.6)
SPOKEN_FILTER = {"true": True, "false": False}  # v4.3: /v1/thoughts?spoken=
INITIATIVE_NONE = {"outcome": "none"}  # v3.4: nothing to say now (no fresh offer, off, or a compose under way)


MAX_KEPT_TURNS = 32  # finished turns remembered by id (for retries after a dropped stream)
_END = ("_end", None)
EVENT_QUEUE_MAX = 256  # per listener; a full queue drops its oldest event — a slow client never stalls a turn
HEARTBEAT_S = 15.0  # a ':' comment after this much silence keeps the stream honest (and cancellable)
ORIGIN_MAX = 64  # an X-Lumi-Client id longer than this is cut
BG_WAIT_S = 300.0  # v2.5: how long a client request waits for the server's own act (a scheduled thought…)
REPLAY_MAX = 50  # v2.5: the conversation events a fresh listener gets first — what happened meanwhile
_REPLAYABLE = ("message", "thought", "voice")  # v4.6: a replayed line that names a voice gets its readiness too
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def sse(event: str, data: dict[str, Any], eid: str | None = None) -> str:
    """One Server-Sent Event — ``data`` as one JSON line (newline-safe); ``eid`` → its ``id:`` (v2.5)."""
    head = f"event: {event}\n" + (f"id: {eid}\n" if eid else "")
    return f"{head}data: {json.dumps(data, ensure_ascii=False)}\n\n"


@dataclass(frozen=True)
class Outbox:
    """v2.5: the file bus the server writes in server mode — it is the single outbox writer then."""

    path: Path
    mirror_user: bool = False  # the Telegram bridge is on: your client lines go to Telegram too


class _Item(tuple):
    """One queued event: unpacks as the v2.4 ``(event, data)`` pair, its SSE id (``eid``) and sequence number
    riding along (v2.5)."""

    def __new__(cls, event: str, data: dict[str, Any], eid: str | None = None, seq: int = 0) -> _Item:
        item = super().__new__(cls, (event, data))
        item.eid, item.seq = eid, seq
        return item


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
    server's shutdown, so a listening client never holds a graceful stop open. v2.5: every event gets an
    SSE id ``<boot>-<seq>``, and the last ``REPLAY_MAX`` conversation events (``message`` / ``thought``) are
    kept for listeners that ask for a replay — what happened meanwhile, or what a dropped stream missed."""

    def __init__(self, maxsize: int = EVENT_QUEUE_MAX, replay_max: int = REPLAY_MAX) -> None:
        self._maxsize = maxsize
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._closed = False
        self.boot = uuid.uuid4().hex[:8]  # another boot's id → the listener starts over (the whole buffer)
        self._seq = 0
        self._replay: deque[_Item] = deque(maxlen=replay_max)

    def _seq_after(self, last_event_id: str | None) -> int:
        """The sequence a resuming listener saw last — 0 (replay everything) for none / another boot."""
        boot, _, seq = (last_event_id or "").partition("-")
        return int(seq) if boot == self.boot and seq.isdigit() else 0

    def subscribe(self, *, replay: bool = False, last_event_id: str | None = None) -> queue.Queue:
        """A listener's queue. With ``replay`` it starts with the conversation events after ``last_event_id``
        (all kept ones for a fresh listener), flagged ``replay: true`` — atomic with ``publish``, so nothing
        is missed and nothing comes twice."""
        sub: queue.Queue = queue.Queue(maxsize=self._maxsize)
        with self._lock:
            if self._closed:
                _offer(sub, _END)  # a listener arriving during shutdown ends at once
                return sub
            if replay:
                after = self._seq_after(last_event_id)
                for item in self._replay:
                    if item.seq > after:
                        _offer(sub, _Item(item[0], {**item[1], "replay": True}, item.eid, item.seq))
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

    @property
    def closed(self) -> bool:
        """Whether the server is stopping (v2.6: a new listener is turned away, never handed a last state)."""
        with self._lock:
            return self._closed

    def publish(self, event: str, data: dict[str, Any]) -> None:
        with self._lock:  # one order for every listener (and the replay buffer)
            self._seq += 1
            item = _Item(event, data, f"{self.boot}-{self._seq}", self._seq)
            if event in _REPLAYABLE:
                self._replay.append(item)
            for sub in self._subs:
                _offer(sub, item)

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
        yield sse(item[0], item[1], getattr(item, "eid", None))


class _TurnRecord:
    """One turn's lifecycle: streamed events while it runs, then its result (or error), kept by id."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.events: queue.Queue = queue.Queue()
        self.result: dict[str, Any] | None = None
        self.error: tuple[int, str] | None = None
        self.received: float | None = None  # v2.6: when the request arrived (monotonic) — the turn's server_ms


class CommandRequest(BaseModel):
    line: str
    confirmed: bool = False


class DirectiveRequest(BaseModel):
    line: str  # a typed %directive, e.g. "%think! про море"


def directive_payload(outcome: Any) -> dict[str, Any]:
    """A ``DirectiveOutcome`` as JSON — the thought's own fields only (its originating user stays inside)."""
    thought = outcome.thought
    return {
        "is_directive": outcome.is_directive, "mode": outcome.mode, "saved_to": outcome.saved_to,
        "thought": None if thought is None else {
            "kind": thought.kind, "text": thought.text, "emotion": thought.emotion, "when": thought.when,
        },
    }


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


def state_snapshot(core: Any, session: Any, *, env: str | None, version: str | None,
                   thumbs_dir: Path | None = None, schedule: bool = False, actions: bool = False,
                   voice: dict[str, bool] | None = None) -> dict[str, Any]:
    """Everything a client's status/stats lines read — one fetch per turn/command, not one per render. v4.6: ``voice``
    ``{enabled, available}`` — her voice's switch — only on a server that can voice her (absent otherwise)."""
    files_served = isinstance(getattr(core, "files_root", None), Path)
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
        "cycle": cycle_brief(core),  # v4.4: today's hormonal phase for the portrait panel, or None
        # v2.6: what the SERVER can do — a client on another machine gates /image and /web on these, not its .env
        "features": {"image": bool(getattr(core, "image_enabled", False)),
                     "web_lookup": bool(getattr(core, "web_lookup_enabled", False)),
                     # v4.1: her sandbox is served to the window's views (read-only) — whatever her file tools may do
                     "files": files_served,
                     # v4.2: her pictures and their thumbnails (the sandbox and the cache outside it are both served)
                     "gallery": files_served and thumbs_dir is not None,
                     # v4.3: her journal (the owner's only, by rule) and her thought stream (unless /thoughts is off)
                     "journal": journal_served(core),
                     "thoughts": thoughts_served(core),
                     # v4.4: her day (mood, biorhythms, closeness) and her timetable (while a scheduler runs)
                     "today": callable(getattr(core, "today_view", None)),
                     "schedule": schedule,
                     # v4.5: the owner's actions on her sandbox (the sandbox and the action log both configured)
                     "actions": files_served and actions},
    } | ({"voice": voice} if voice is not None else {})


def cycle_brief(core: Any) -> dict[str, Any] | None:
    """v4.4 LUMI-319: ``{phase, day, length}`` of today's hormonal cycle (``Core.cycle_view``), or ``None``."""
    view = getattr(core, "cycle_view", None)
    detail = view() if callable(view) else None
    return {k: detail[k] for k in ("phase", "day", "length")} if detail else None


def journal_served(core: Any) -> bool:
    """v4.3: the journal is served when a journal root exists in config and the resolved user may read it."""
    return isinstance(getattr(core, "journal_root", None), Path) and may_read_journal(str(getattr(core, "user_id", "")))


def thoughts_served(core: Any) -> bool:
    """v4.3: the thought stream is served unless the owner turned the thought view off (``thoughts.show: off``)."""
    return getattr(core, "thoughts_show", "hidden") != "off"


def turn_pictures(core: Any) -> list[str]:
    """v4.2: the sandbox paths of the pictures she made or sent in the turn just run (``[]`` when none)."""
    pictures = getattr(core, "last_pictures", None)
    return [p for p in pictures if isinstance(p, str)] if isinstance(pictures, list) else []


def turn_timing(core: Any, received: float | None) -> dict[str, Any] | None:
    """The turn's stages on the server (v2.6) — the core's PRE / MODEL / POST and their total, plus ``server_ms``:
    request received → outcome, any wait for her own act included. A client subtracts it from its own wall time
    to see the network. ``None`` when the core kept no timing."""
    timing = getattr(core, "last_turn_timing", None)
    if not timing:
        return None
    out: dict[str, Any] = {k: timing.get(k) for k in ("pre_ms", "llm_ms", "post_ms", "total_ms")}
    if received is not None:
        out["server_ms"] = int((time.monotonic() - received) * 1000)
    return out


class _Service:
    """The one core + its current session, behind one lock."""

    def __init__(self, core: Any, session: Any, *, env: str | None, version: str | None,
                 outbox: Outbox | None = None, thumbs_dir: Path | None = None,
                 actions_log: ActionLog | None = None, voice: VoiceSetup | None = None) -> None:
        self.core = core
        self.outbox = outbox  # v2.5: the file bus the server writes (Telegram / the voicer), or None
        # v4.6: the one ordered outbox writer — and her voice, while the switch is on (no setup → never voiced)
        self.voice_switch = (VoiceSwitch(voice.switch_path, first=voice.first, available=voice.tts is not None)
                             if voice is not None else None)
        self.voice_store = VoiceStore(voice.root) if voice is not None else None
        self.voicer = LineVoicer(outbox.path if outbox is not None else None, switch=self.voice_switch,
                                 store=self.voice_store, tts=voice.tts if voice is not None else None,
                                 user_id=str(getattr(core, "user_id", "owner")), on_ready=self._voice_ready)
        self.thumbs_dir = thumbs_dir  # v4.2: the thumbnail cache (per user below it), outside her sandbox; or None
        self.actions_log = actions_log  # v4.5: the owner's action log (<data root>/actions.jsonl), or None
        self.session = session
        self.env = env
        self.version = version
        self.lock = threading.Lock()
        self._bg = False  # v2.5: the lock is held by the server's own act (the brain host), not a client
        self.last_input = core.clock()  # v2.5: the last real input (a client turn / directive, a Telegram line)
        # v4.4: the scheduler's view (``now`` → dict), registered by the brain host while it runs a scheduler; or None
        self.schedule_view: Callable[[Any], dict[str, Any]] | None = None
        self._turns: OrderedDict[str, _TurnRecord] = OrderedDict()  # v2.3: turn_id → record
        self._turns_lock = threading.Lock()
        self.bus = EventBus()  # v2.4: the push channel
        self.last_state = self.state()  # the last consistent snapshot — what a new listener starts from

    def state(self) -> dict[str, Any]:
        return state_snapshot(self.core, self.session, env=self.env, version=self.version, thumbs_dir=self.thumbs_dir,
                              schedule=self.schedule_view is not None, actions=self.actions_log is not None,
                              voice=self.voice_switch.view() if self.voice_switch is not None else None)

    def _voice_ready(self, voice_id: str, ok: bool) -> None:
        """v4.6: a line's audio is ready (or failed) — the ``voice`` push, after that line's ``message``."""
        self.bus.publish("voice", {"message": voice_id, "ready": bool(ok)})

    def acquire(self) -> None:
        """A client request's turn at the lock: at once; or — while the server's own act holds it (v2.5) —
        after that act, bounded by ``BG_WAIT_S``; another client's request → ``409 busy`` (one at a time)."""
        if self.lock.acquire(blocking=False):
            return
        if self._bg and self.lock.acquire(timeout=BG_WAIT_S):
            return
        raise HTTPException(status_code=409, detail="busy")

    @contextmanager
    def background(self) -> Iterator[None]:
        """v2.5: the lock for the server's own act (a scheduled thought, a Telegram turn): it waits for a
        client turn to finish; a client request arriving meanwhile waits for it instead of getting 409."""
        self.lock.acquire()
        self._bg = True
        try:
            yield
        finally:
            self._bg = False
            self.lock.release()

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
        with self.background():
            self.publish_state()

    def mirror(self, write: Callable[..., Any], *args: Any) -> None:
        """Write to the outbox (v2.5) — best-effort: a full disk never fails a turn. v4.6: through the one ordered
        writer (``LineVoicer``) — at once, or behind a line of hers still being voiced."""
        self.voicer.write(write, *args)

    def her_line(self, state: Any, speech: str | None, voice_id: Any = AUTO) -> str | None:
        """v4.6: her reply to the outbox — and, with a voice id (``voicer.new_id()``, taken while the switch is on),
        its voice, made once after the line is shown. Returns the line's voice id, or ``None``."""
        return self.voicer.line(mirror_reply, state, speech, text=speech or state.reply,
                                emotion=state.emotion.value, voice_id=voice_id)

    def her_thought(self, text: str, emotion: str) -> str | None:
        """v4.6: a shown thought to the outbox (``scheduler.show_to_outbox``), voiced like her lines while on."""
        return self.voicer.line(mirror_thought, text, emotion, text=text, emotion=emotion)

    def publish_message(self, role: str, text: str, source: str, *, state: Any = None,
                        origin: str | None = None, pictures: list[str] | None = None,
                        voice: str | None = None) -> None:
        """v2.5: one conversation line as a ``message`` event (a hidden seed is never one). v4.2: her line carries
        ``pictures`` — the sandbox paths of what she made or sent in that turn — only when there are any. v4.6: and
        ``voice`` — its voice id — while the switch is on; a ``voice`` push follows when the audio is ready."""
        data: dict[str, Any] = {"role": role, "text": text, "source": source}
        if state is not None:
            data.update(emotion=state.emotion.value, intensity=state.intensity)
        if pictures:
            data["pictures"] = list(pictures)
        if voice:
            data["voice"] = voice
        data["origin"] = origin
        self.bus.publish("message", data)

    def _publish_state_quietly(self, origin: str | None) -> None:
        try:
            self.publish_state(origin)
        except Exception:  # noqa: BLE001 — a failed push never keeps the lock or kills the runner
            log.warning("publishing the state failed", exc_info=True)

    # --- v2.3 turns: one runner thread each, kept by id -------------------------------------------------
    def begin_turn(self, req: TurnRequest, *, stream: bool,
                   origin: str | None = None) -> tuple[_TurnRecord, bool]:
        """The record for this turn and whether it is NEW. A known ``turn_id`` returns the existing record
        (running or done) without running anything; a new turn takes the lock — 409 while another client's
        request runs, a wait while the server's own act does (v2.5; never inside the registry lock)."""
        received = time.monotonic()  # v2.6: server_ms counts from here — a wait for her own act included
        with self._turns_lock:
            if req.turn_id and req.turn_id in self._turns:
                return self._turns[req.turn_id], False
            got = self.lock.acquire(blocking=False)
            if not got and not self._bg:
                raise HTTPException(status_code=409, detail="busy")
        if not got and not self.lock.acquire(timeout=BG_WAIT_S):  # the server's own act — wait it out
            raise HTTPException(status_code=409, detail="busy")
        with self._turns_lock:
            if req.turn_id and req.turn_id in self._turns:  # registered while we waited — never run twice
                self.lock.release()
                return self._turns[req.turn_id], False
            record = _TurnRecord()
            record.received = received
            if req.turn_id:
                self._turns[req.turn_id] = record
                self._forget_old_turns()
        self.last_input = self.core.clock()  # a client turn is real input (the idle triggers' anchor)
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
            if self.outbox is not None and self.outbox.mirror_user:
                self.mirror(mirror_user, req.text)  # your line → Telegram first, as in-process keyboard turns
            result = self.core.reply(req.text, self.session, images=req.images, **hooks)
            voice_id = self.voicer.new_id()  # v4.6: taken now — the message names it, the voice push follows
            self.bus.publish("turn", {"emotion": result.emotion.value, "intensity": result.intensity,
                                      "theme": self.core.theme, "origin": origin})
            self.publish_message("user", req.text, "client", origin=origin,
                                 pictures=req.sandbox_images)  # v4.5: the pictures you showed her
            self.publish_message("lili", result.reply, "client", state=result, origin=origin,
                                 pictures=turn_pictures(self.core), voice=voice_id)
            self.her_line(result, getattr(self.core, "last_speech", None), voice_id)
            record.result = self.turn_payload(result, origin, received=record.received, voice=voice_id)
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

    def turn_payload(self, result: Any, origin: str | None = None, *, received: float | None = None,
                     voice: str | None = None) -> dict[str, Any]:
        """The turn's outcome; its ``state`` is the snapshot just pushed (taken under the runner's lock)."""
        core = self.core
        payload = {
            "reply": result.reply, "emotion": result.emotion.value, "intensity": result.intensity,
            "thinking": getattr(core, "last_thinking", None), "intent": getattr(core, "last_intent", None),
            "style": core.style, "stats": stats_dict(core.last_stats),
            "timing": turn_timing(core, received),  # v2.6: the client's /latency sees the network hop
            "state": self.publish_state(origin),
        }
        if pictures := turn_pictures(core):  # v4.2: only when she made or sent a picture this turn
            payload["pictures"] = pictures
        if getattr(core, "tool_trace", False):  # v4.4: the tools of this reply, while files.trace is on (else absent)
            payload["tool_calls"] = tool_calls_view(getattr(core, "last_tool_calls", None) or ())
        if voice:  # v4.6: her line's voice id, while the switch is on (else absent — the outcome byte-identical)
            payload["voice"] = voice
        return payload


def _room_turn(space: Any, req: TurnRequest) -> dict[str, Any]:
    """v3.1: a turn in the agora channel — the room's own path (``core.space.AgoraSpace``), never ``Core.reply``,
    never the service's lock, the outbox, the event bus, the idle clock or his state. It runs on its own thread; the
    request waits the room's budget plus a grace, then gives up (``504``) and the turn's reply is dropped."""
    from core.emotion import EmotionError
    from core.space import SpaceBusy, SpaceCapped, SpaceTimeout, speaker_problem

    if problem := speaker_problem(req.speaker):
        raise HTTPException(status_code=422, detail=problem)
    if not req.turn_id or not req.turn_id.strip():
        raise HTTPException(status_code=422, detail="a room turn needs its turn_id (the trigger's event id)")
    if req.images or req.sandbox_images:
        raise HTTPException(status_code=422, detail="a room turn carries no images")
    from core.space import REGISTERS

    if req.register_ is not None and req.register_ not in REGISTERS:  # v3.3: missing → "voice", the room's default
        raise HTTPException(status_code=422, detail=f"a room turn's register is one of {', '.join(REGISTERS)}")
    if not req.text.strip():
        raise HTTPException(status_code=422, detail="a room turn needs its line")
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["outcome"] = space.turn(req.speaker, req.text, req.turn_id)
        except BaseException as exc:  # noqa: BLE001 — handed to the request thread, mapped below
            box["error"] = exc

    runner = threading.Thread(target=run, daemon=True, name="lumi-agora-turn")
    runner.start()
    runner.join(space.timeout_s + ROOM_GRACE_S)
    if runner.is_alive():
        space.abandon(req.turn_id)
        raise HTTPException(status_code=504, detail="the room's turn timed out")
    error = box.get("error")
    if error is None:
        return box["outcome"].as_dict()
    if isinstance(error, SpaceBusy):
        raise HTTPException(status_code=409, detail=ROOM_BUSY)
    if isinstance(error, SpaceCapped):  # v3.3: the hourly cap — silence in the room
        raise HTTPException(status_code=429, detail="the room's hourly cap is reached")
    if isinstance(error, SpaceTimeout):
        raise HTTPException(status_code=504, detail="the room's turn timed out")
    if isinstance(error, LLMError):
        log.warning("room turn: the model failed: %s", error)
        raise HTTPException(status_code=502, detail="model unavailable")
    if isinstance(error, EmotionError):
        log.warning("room turn: no usable reply (%s)", error)
    else:
        log.error("room turn failed", exc_info=error)
    raise HTTPException(status_code=500, detail="the room's turn failed — see the server log")


def create_app(core: Any, *, token: str, session: Any = None, env: str | None = None,
               version: str | None = None, on_new_session: Callable[[], None] | None = None,
               outbox: Outbox | None = None, space: Any = None, room_token: str | None = None,
               thumbs_dir: Path | None = None, actions_log: Path | None = None,
               voice: VoiceSetup | None = None) -> FastAPI:
    """The v1 app around an injected core (a mock model in tests). ``token`` is required.

    ``on_new_session`` runs in the background after ``/v1/session/new`` (the ambient re-snapshot, as the
    TUI's ``/new`` always did) — best-effort, never delaying the response.

    v3.1: ``space`` (an ``AgoraSpace``) turns the Agora space on — ``POST /v1/observe``, a turn with ``channel:
    "agora"`` and ``"agora": AGORA_API`` on health (v3.3: + retract; v3.4: + her initiative — ``GET /v1/initiative``
    and ``POST /v1/initiative/{initiative_id}/posted``); ``room_token`` is the room's scoped token (its routes only,
    ``403`` elsewhere). Without ``space`` the app is today's, route for route.

    v4.2: ``thumbs_dir`` is the thumbnail cache (``<data root>/cache/thumbs``; the user's folder below it), outside her
    sandbox. ``None`` → the thumbnail route answers 404 and ``features.gallery`` is false."""
    if not token:
        raise ValueError("LUMI_SERVER_TOKEN is required to serve")
    if space is not None and (not room_token or room_token == token):
        raise ValueError("the Agora space needs its own room token (agora.token), never the client token")
    service = _Service(core, session if session is not None else core.start_session(), env=env, version=version,
                       outbox=outbox, thumbs_dir=Path(thumbs_dir) if thumbs_dir is not None else None,
                       actions_log=ActionLog(actions_log) if actions_log is not None else None, voice=voice)
    expected = f"Bearer {token}".encode()
    room_expected = f"Bearer {room_token}".encode() if space is not None else None
    # No interactive docs / schema endpoints: the API is private to its own clients.
    app = FastAPI(title="Lumi", version=version or "dev", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service = service
    app.state.space = space

    def _is(authorization: str | None, wanted: bytes | None) -> bool:
        return bool(authorization and wanted and secrets.compare_digest(authorization.encode(), wanted))

    def require_token(authorization: str | None = Header(default=None)) -> None:
        if _is(authorization, expected):
            return
        if _is(authorization, room_expected):  # v3.1: the room's token never reaches his routes
            raise HTTPException(status_code=403, detail=ROOM_ONLY)
        raise HTTPException(status_code=401, detail="unauthorized")

    def caller(authorization: str | None = Header(default=None)) -> str:
        """v3.1: who calls a route both may reach (a turn, observe) — ``"owner"`` or ``"room"``."""
        if _is(authorization, expected):
            return "owner"
        if _is(authorization, room_expected):
            return "room"
        raise HTTPException(status_code=401, detail="unauthorized")

    guarded = [Depends(require_token)]

    @app.get("/v1/health")
    def health() -> dict[str, Any]:
        body: dict[str, Any] = {"ok": True, "env": env, "version": version}
        if space is not None:
            body["agora"] = AGORA_API  # v3.1: the agora space API version, only while the space is on
        return body

    @app.get("/v1/state", dependencies=guarded)
    def state() -> dict[str, Any]:
        """The current state — fresh when idle; while a turn runs, the last consistent snapshot."""
        if not service.lock.acquire(blocking=False):
            return service.last_state
        try:
            return service.publish_state()
        finally:
            service.lock.release()

    # --- v4.1 LUMI-302: her sandbox for the window's views — read-only, never the turn lock, never a model call ---
    def _files_root() -> Path:
        root = getattr(core, "files_root", None)
        if not isinstance(root, Path):
            raise HTTPException(status_code=404, detail="files are not served")
        return root

    def _mapped(read: Callable[[], Any]) -> Any:
        try:
            return read()
        except BrowseError as exc:  # a readable line naming the sandbox path only — never an absolute one
            status = next((code for kind, code in BROWSE_STATUS if isinstance(exc, kind)), 400)
            raise HTTPException(status_code=status, detail=str(exc)) from None
        except OSError:
            raise HTTPException(status_code=404, detail="the file can't be read") from None

    def _browse(read: Callable[..., Any], path: str) -> Any:
        return _mapped(lambda: read(_files_root(), path))

    @app.get("/v1/files", dependencies=guarded)
    def files(path: str = "") -> JSONResponse:
        """A folder of her sandbox: ``{path, entries, truncated}`` (``core.browse.list_folder``)."""
        return JSONResponse(_browse(list_folder, path), headers=FILES_JSON_HEADERS)

    @app.get("/v1/files/text", dependencies=guarded)
    def files_text(path: str = "") -> JSONResponse:
        """A text file for the reader: ``{path, text, size, cut, type}`` (``core.browse.read_text``)."""
        return JSONResponse(_browse(read_text, path), headers=FILES_JSON_HEADERS)

    @app.get("/v1/files/raw", dependencies=guarded)
    def files_raw(path: str = "") -> Response:
        """A picture's bytes (png/jpeg/gif/webp by magic bytes; ``core.browse.read_picture``) — never SVG or HTML."""
        data, media_type = _browse(read_picture, path)
        return Response(content=data, media_type=media_type, headers=FILES_RAW_HEADERS)

    # --- v4.2 LUMI-306: her pictures for the Gallery — the same rules: read-only over the sandbox, never the lock ---
    @app.get("/v1/images", dependencies=guarded)
    def images(before: str | None = None, limit: int = GALLERY_MAX_LIMIT, folder: str | None = None) -> JSONResponse:
        """Her pictures, newest first: ``{items, next, total, folders, truncated}`` (``core.gallery.list_pictures``)."""
        root = _files_root()
        return JSONResponse(_mapped(lambda: list_pictures(root, before=before, limit=limit, folder=folder)),
                            headers=FILES_JSON_HEADERS)

    @app.get("/v1/images/thumb", dependencies=guarded)
    def images_thumb(path: str = "") -> Response:
        """A picture's 320 px JPEG thumbnail, cached in ``thumbs_dir/<user_id>`` (``core.gallery.thumbnail``)."""
        root = _files_root()
        if service.thumbs_dir is None:
            raise HTTPException(status_code=404, detail="thumbnails are not served")
        cache = service.thumbs_dir / str(core.user_id)
        try:
            data = _mapped(lambda: thumbnail(root, path, cache))
        except ValueError:  # a cache configured inside her sandbox: refused, never written there
            log.warning("the thumbnail cache %s is inside the sandbox — not served", cache)
            raise HTTPException(status_code=404, detail="thumbnails are not served") from None
        return Response(content=data, media_type="image/jpeg", headers=FILES_RAW_HEADERS)

    # --- v4.3 LUMI-311: her journal and her thoughts for the window — read-only, never the lock, no model call ---
    def _journal_root() -> Path:
        if not may_read_journal(str(core.user_id)):  # the role check after the token: never another user's read
            raise HTTPException(status_code=403, detail=JOURNAL_OWNER_ONLY)
        root = getattr(core, "journal_root", None)
        if not isinstance(root, Path):
            raise HTTPException(status_code=404, detail="the journal is not served")
        return root

    @app.get("/v1/journal", dependencies=guarded)
    def journal() -> JSONResponse:
        """Her journal's days, newest first: ``{days, truncated}`` (``core.journal.list_days``). Owner-only."""
        root = _journal_root()
        return JSONResponse(_mapped(lambda: list_days(root)), headers=FILES_JSON_HEADERS)

    @app.get("/v1/journal/{date}", dependencies=guarded)
    def journal_day(date: str) -> JSONResponse:
        """One day, parsed: ``{date, stamp, sections, markdown}`` (``core.journal.read_day``). Owner-only."""
        root = _journal_root()
        return JSONResponse(_mapped(lambda: read_day(root, date)), headers=FILES_JSON_HEADERS)

    @app.get("/v1/thoughts", dependencies=guarded)
    def thoughts(before: str | None = None, limit: int = THOUGHT_PAGE_MAX,
                 kind: Annotated[list[str] | None, Query()] = None,
                 spoken: str | None = None, date: str | None = None) -> JSONResponse:
        """One page of her thought stream, newest first: ``{items, next, stats, days, kinds}``
        (``Core.thoughts_page`` — the surfacing filter, so a thought sparked with another user never shows)."""
        if not thoughts_served(core):
            raise HTTPException(status_code=404, detail="thoughts are not served")
        if spoken not in (None, "") and spoken not in SPOKEN_FILTER:
            raise HTTPException(status_code=400, detail="spoken is true or false")
        try:
            page = core.thoughts_page(before=before or None, limit=limit, kinds=kind or (),
                                      spoken=SPOKEN_FILTER.get(spoken or ""), date=date or None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return JSONResponse(page, headers=FILES_JSON_HEADERS)

    # --- v4.4 LUMI-316: her day and her timetable for the window — read-only, never the lock, no model call ---
    @app.get("/v1/today", dependencies=guarded)
    def today() -> JSONResponse:
        """Her day: ``{date, mood, biorhythms, closeness, plan}`` (``Core.today_view`` — never computes the mood)."""
        return JSONResponse(core.today_view(), headers=FILES_JSON_HEADERS)

    @app.get("/v1/schedule", dependencies=guarded)
    def schedule() -> JSONResponse:
        """Her timetable: ``{now, quiet_hours, day_cap, fired_today, entries}`` (the brain host's
        ``Scheduler.view``); no scheduler running → 404."""
        view = service.schedule_view
        if view is None:
            raise HTTPException(status_code=404, detail="the schedule is not served")
        return JSONResponse(view(core.clock()), headers=FILES_JSON_HEADERS)

    # --- v4.5 LUMI-322: the owner's actions on her sandbox — create-only, nothing deleted, every action logged ---
    def _actions() -> tuple[Path, ActionLog]:
        root = _files_root()
        if service.actions_log is None:
            raise HTTPException(status_code=404, detail="actions are not served")
        return root, service.actions_log

    def _log(action: str, done: dict[str, Any], client: str | None, size: int | None = None) -> None:
        if service.actions_log is None:  # the reads (a download, a shown picture) are served without a log
            return
        try:
            service.actions_log.append({"ts": core.clock().isoformat(timespec="seconds"), "action": action,
                                        "path": done.get("path"), "to": done.get("to"), "size": size,
                                        "client": origin_of(client)})
        except OSError:
            log.warning("the action log could not be written", exc_info=True)

    @app.post("/v1/files/upload", dependencies=guarded)
    async def files_upload(request: Request, path: str = "", name: str = "",
                           x_lumi_client: str | None = Header(default=None)) -> JSONResponse:
        """A file from the owner's Mac into her sandbox: the raw body → ``{path, size}`` (``core.actions.upload``:
        create-only under a free name, pictures ≤ 10 MB by magic bytes, UTF-8 text ≤ 1 MB). An oversized body is
        refused while it streams in, never held whole."""
        root, _ = _actions()
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > UPLOAD_MAX_PICTURE:
                raise HTTPException(status_code=413, detail="the file is larger than 10 MB")
        done = _mapped(lambda: upload(root, path, name, bytes(data)))
        _log("upload", done, x_lumi_client, size=done.get("size"))
        return JSONResponse(done, headers=FILES_JSON_HEADERS)

    @app.post("/v1/files/rename", dependencies=guarded)
    def files_rename(req: RenameRequest, x_lumi_client: str | None = Header(default=None)) -> JSONResponse:
        """Rename a file or a folder within its folder: ``{path, to}`` — never over a taken name (409)."""
        root, _ = _actions()
        done = _mapped(lambda: rename(root, req.path, req.name))
        _log("rename", done, x_lumi_client)
        return JSONResponse(done, headers=FILES_JSON_HEADERS)

    @app.post("/v1/files/trash", dependencies=guarded)
    def files_trash(req: PathRequest, x_lumi_client: str | None = Header(default=None)) -> JSONResponse:
        """Move a file or a folder into ``.trash/<today>/``: ``{path, to}``. Nothing is deleted."""
        root, _ = _actions()
        done = _mapped(lambda: trash(root, req.path, core.clock().date().isoformat()))
        _log("trash", done, x_lumi_client)
        return JSONResponse(done, headers=FILES_JSON_HEADERS)

    @app.post("/v1/files/restore", dependencies=guarded)
    def files_restore(req: PathRequest, x_lumi_client: str | None = Header(default=None)) -> JSONResponse:
        """Move an item from ``.trash/`` back to where the action log says it came from: ``{path, to}``."""
        root, actions_log = _actions()
        done = _mapped(lambda: restore(root, req.path, actions_log.original_of(req.path)))
        _log("restore", done, x_lumi_client)
        return JSONResponse(done, headers=FILES_JSON_HEADERS)

    @app.get("/v1/files/download", dependencies=guarded)
    def files_download(path: str = "", x_lumi_client: str | None = Header(default=None)) -> Response:
        """Any regular file of her sandbox up to 50 MB, as an attachment (read-only; logged as ``download``)."""
        root = _files_root()

        def read() -> tuple[bytes, str]:
            target = browse_resolve(root, path)
            shown = path.strip().strip("/")
            if not target.is_file():
                raise NotFound(f"no such file: {shown!r}")
            if target.stat().st_size > DOWNLOAD_MAX:
                raise TooLarge(f"{shown!r} is larger than {DOWNLOAD_MAX // (1024 * 1024)} MB")
            return target.read_bytes(), target.name

        data, name = _mapped(read)
        _log("download", {"path": path.strip().strip("/")}, x_lumi_client, size=len(data))  # code review #1
        ascii_name = "".join(ch if 32 < ord(ch) < 127 and ch not in r'"\;' else "_" for ch in name) or "download"
        disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"
        return Response(content=data, media_type="application/octet-stream",
                        headers={"Content-Disposition": disposition, "X-Content-Type-Options": "nosniff",
                                 "Cache-Control": "private, no-store"})

    def _with_sandbox_images(req: TurnRequest) -> TurnRequest:
        """v4.5: a turn that shows her pictures of her sandbox — each read by path (``read_picture``: ≤ 10 MB, by
        magic bytes) into an image block beside ``images``; a bad path is refused before the turn runs."""
        paths = [p for p in (req.sandbox_images or []) if isinstance(p, str)]
        if not paths:
            return req
        if len(paths) > SANDBOX_IMAGES_MAX:
            raise HTTPException(status_code=400, detail=f"at most {SANDBOX_IMAGES_MAX} pictures a turn")
        if not getattr(core, "image_enabled", False):  # she only sees a turn's pictures while image.enabled is on
            raise HTTPException(status_code=422, detail="she can't see pictures on this server (image.enabled is off)")
        root = _files_root()
        blocks = []
        for rel in paths:
            data, media_type = _mapped(lambda rel=rel: read_picture(root, rel))
            blocks.append(image_block(data, media_type))
        return req.model_copy(update={"images": [*(req.images or []), *blocks], "sandbox_images": paths})

    def _log_shown(req: TurnRequest, fresh: bool, client: str | None) -> None:
        """v4.5 code review #1: each picture shown to her is an action — logged once, for the turn that runs (a
        retried ``turn_id`` returns the stored outcome and logs nothing)."""
        if fresh:
            for rel in req.sandbox_images or []:
                _log("show", {"path": rel.strip().strip("/")}, client)

    # --- v4.6 LUMI-328: her voice — the audio of a line, the switch; owner-only, never the turn lock ---
    @app.get("/v1/voice/{voice_id}", dependencies=guarded)
    def voice_audio(voice_id: str) -> Response:
        """One line's audio (``audio/mpeg``) from ``<data root>/voice/<user_id>/<id>.mp3`` — 404 for anything else."""
        if service.voice_store is None or not VOICE_ID_RE.fullmatch(voice_id):
            raise HTTPException(status_code=404, detail="no such voice")
        data = service.voice_store.read(str(core.user_id), voice_id)
        if data is None:
            raise HTTPException(status_code=404, detail="no such voice")
        return Response(content=data, media_type="audio/mpeg", headers=VOICE_HEADERS)

    @app.post("/v1/voice", dependencies=guarded)
    def voice_switch(req: VoiceRequest) -> dict[str, bool]:
        """The «Voice» switch: on → her next lines are voiced (the window's ▶, Telegram's voice), off → text. Persisted;
        409 while the server has no ElevenLabs key or voice id."""
        if service.voice_switch is None:
            raise HTTPException(status_code=404, detail=VOICE_NOT_SERVED)
        try:
            view = service.voice_switch.set(req.enabled)
        except VoiceUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        if service.lock.acquire(blocking=False):  # the new state now; a running turn pushes it when it ends
            try:
                service._publish_state_quietly(None)
            finally:
                service.lock.release()
        return view

    @app.get("/v1/events", dependencies=guarded)
    def events(last_event_id: str | None = Header(default=None, alias="Last-Event-ID")) -> StreamingResponse:
        """v2.4: the push channel (SSE) — the current ``state`` first, then events as they happen, ``:``
        heartbeats in between; it ends only when the client leaves or the server stops. v2.5: after the
        state comes the replay — the conversation a fresh listener missed (or, with ``Last-Event-ID``, what
        a dropped stream missed)."""

        def body() -> Iterator[str]:
            # subscribe before reading the state: nothing published in between is lost
            sub = service.bus.subscribe(replay=True, last_event_id=last_event_id)
            try:
                if service.bus.closed:  # stopped since the check below — no last state frame either
                    return
                yield from event_frames(sub, ("state", {"origin": None, "state": service.last_state}))
            finally:
                service.bus.unsubscribe(sub)

        # v2.6: while the server stops (it still accepts for a moment after the streams are closed) a reconnecting
        # client is turned away — handed a last state, it would read the stop as "the pushes are back".
        if service.bus.closed:
            raise HTTPException(status_code=503, detail="the server is stopping")
        return StreamingResponse(body(), media_type="text/event-stream", headers=_SSE_HEADERS)

    @app.post("/v1/turn")
    def turn(req: TurnRequest, x_lumi_client: str | None = Header(default=None),
             who: str = Depends(caller)) -> dict[str, Any]:
        """A blocking turn — or, for a known ``turn_id``, the stored result of that turn (never a rerun).
        v3.1: ``channel: "agora"`` runs the room's own path; the room's token reaches only that channel."""
        if req.channel == "agora":
            if space is None:
                raise HTTPException(status_code=404, detail="Not Found")  # the space is off: its route is absent
            return _room_turn(space, req)
        if who == "room":
            raise HTTPException(status_code=403, detail=ROOM_ONLY)
        req = _with_sandbox_images(req)
        record, fresh = service.begin_turn(req, stream=False, origin=origin_of(x_lumi_client))
        _log_shown(req, fresh, x_lumi_client)
        record.done.wait()
        if record.error is not None:
            raise HTTPException(status_code=record.error[0], detail=record.error[1])
        return record.result

    if space is not None:
        @app.post("/v1/observe")
        def observe(req: ObserveRequest, who: str = Depends(caller)) -> dict[str, int]:
            """v3.1: the room's lines she hears but does not answer — stored, idempotent by ``id``, embedded;
            no model call, nothing on his side."""
            from datetime import datetime

            from core.space import ObservedLine, speaker_problem

            if len(req.lines) > OBSERVE_BATCH_MAX:
                raise HTTPException(status_code=422, detail=f"at most {OBSERVE_BATCH_MAX} lines a call")
            for line in req.lines:
                if not line.id.strip():
                    raise HTTPException(status_code=422, detail="a line needs its id")
                if problem := speaker_problem(line.speaker):
                    raise HTTPException(status_code=422, detail=problem)
                try:
                    when = datetime.fromisoformat(line.ts)
                except ValueError:
                    raise HTTPException(status_code=422, detail="a line's ts is ISO-8601") from None
                if when.tzinfo is None:  # Q9: with its offset — a naive ts would sort as UTC (code review #5)
                    raise HTTPException(status_code=422, detail="a line's ts carries its offset (Q9)")
            stored, known = space.observe([ObservedLine(ln.id, ln.speaker, ln.text, ln.ts) for ln in req.lines])
            return {"stored": stored, "known": known}

    if space is not None:
        @app.post("/v1/turn/{turn_id}/retract")
        def retract(turn_id: str, who: str = Depends(caller)) -> dict[str, bool]:
            """v3.3: the room takes back her reply to ``turn_id`` — always a 200 (R8): ``{retracted: true}`` when a
            stored reply was removed (or a running one will be), ``{retracted: false}`` when nothing was stored.
            The room's own route: the owner's token gets 403 — his turns are never retractable."""
            if who != "room":
                raise HTTPException(status_code=403, detail="retract is the room's own route")
            return {"retracted": space.retract(turn_id)}

    if space is not None:
        @app.get("/v1/initiative")
        def initiative(who: str = Depends(caller)) -> dict[str, Any]:
            """v3.4: does she want to open a line? A fresh offer (``say``) or ``none`` — never a model call (a compose,
            when Lumi's guards allow, runs in the background for a later poll). The room's own route."""
            if who != "room":
                raise HTTPException(status_code=403, detail="the initiative is the room's own route")
            try:
                offer = space.poll_initiative()
            except Exception:  # noqa: BLE001 — a poll is always answerable: nothing to say
                log.warning("the initiative poll failed", exc_info=True)
                return dict(INITIATIVE_NONE)
            return offer.as_say() if offer is not None else dict(INITIATIVE_NONE)

        @app.post("/v1/initiative/{initiative_id}/posted")
        def initiative_posted(initiative_id: str, req: InitiativePostedRequest,
                              who: str = Depends(caller)) -> dict[str, bool]:
            """v3.4: the bridge posted her offer as ``event_id`` — only now is it her line. Always a 200: ``{stored:
            true}`` when stored, ``{stored: false}`` for an unknown or already stored one (idempotent). The room's own
            route."""
            if who != "room":
                raise HTTPException(status_code=403, detail="the initiative is the room's own route")
            if not req.event_id.strip():
                raise HTTPException(status_code=422, detail="an initiative ack needs its event_id")
            try:
                return {"stored": space.initiative_posted(initiative_id, req.event_id)}
            except Exception:  # noqa: BLE001 — review #2/#3: nothing stored, the ack stays possible; always a 200
                log.warning("the initiative ack failed", exc_info=True)
                return {"stored": False}

    @app.post("/v1/turn/stream", dependencies=guarded)
    def turn_stream(req: TurnRequest, x_lumi_client: str | None = Header(default=None)) -> StreamingResponse:
        """A streamed turn as Server-Sent Events: ``delta``/``think`` while she answers, then exactly one
        ``done`` (the blocking payload) or ``error``. A known ``turn_id`` skips straight to its outcome."""
        if req.channel != "owner":
            raise HTTPException(status_code=422, detail="the room's turns are blocking (POST /v1/turn)")
        req = _with_sandbox_images(req)
        record, fresh = service.begin_turn(req, stream=True, origin=origin_of(x_lumi_client))
        _log_shown(req, fresh, x_lumi_client)

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

    @app.post("/v1/directive", dependencies=guarded)
    def directive(req: DirectiveRequest, x_lumi_client: str | None = Header(default=None)) -> dict[str, Any]:
        """v2.4: a typed ``%directive`` runs here, one at a time like a turn. An **open** thought is pushed
        to every listener as ``thought``; a silent one only lands in her diary. Not a known directive →
        ``is_directive: false`` (the client sends the line as chat, as in-process)."""
        origin = origin_of(x_lumi_client)
        service.acquire()
        try:
            service.last_input = service.core.clock()  # a typed directive is real input too
            outcome = service.core.run_directive(req.line, service.session)
            payload = directive_payload(outcome)
            if outcome.is_directive and outcome.mode == "open" and payload["thought"] is not None:
                service.bus.publish("thought", {**payload["thought"], "origin": origin})
            snap = service.publish_state(origin)
        except Exception:  # think() is best-effort already; anything else is one readable line
            log.exception("directive failed")
            raise HTTPException(status_code=500, detail="directive failed — see the server log") from None
        finally:
            service.lock.release()
        return {**payload, "state": snap}

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

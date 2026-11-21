"""Her voice on the server (v4.6 LUMI-327) — each of her lines voiced once, after it is shown, while one switch is on.

Three pieces, all outside her sandbox (her file tools never see them):

* :class:`VoiceSwitch` — ``<data root>/voice.json`` ``{"enabled": bool}``; its first value is ``telegram.voice``.
  ``available`` needs an ElevenLabs key and a voice id; ``active`` is both.
* :class:`VoiceStore` — ``<data root>/voice/<user_id>/<id>.mp3`` (``<id>`` 32 hex), written through a temp file and a
  rename; :meth:`VoiceStore.expire` removes the files older than 30 days, a bounded number per pass.
* :class:`LineVoicer` — the server's one ordered outbox writer. A write runs at once when nothing is queued ahead of
  it; her line, while the switch is on, is queued: the worker synthesizes it (``speech`` before ``text``, capped, with
  the validated emotion), stores the file, then appends her outbox record naming it (``voice: "<id>.mp3"``, or
  ``null`` when the switch is off or the generation failed). Whatever is written after a line waits behind it, so the
  outbox keeps the order of the lines, and only one thread ever appends.

A generation is one ElevenLabs call (``voice/tts.py``); a failure leaves the text everywhere and never touches the
turn. The log gets one line per generation — its id, its length and its time, never the text or the key — and
(v5.1) the media ledger one ``tts`` record (the characters voiced, the model, the outcome, and the origin and channel
of the work that made the line, captured when the line was queued: the worker thread doesn't inherit them).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from core import ledgers, usage_origin

log = logging.getLogger("lumi.voice")

VOICE_ID_RE = re.compile(r"[0-9a-f]{32}")
VOICE_FILE_RE = re.compile(r"[0-9a-f]{32}\.mp3")
_TEMP_RE = re.compile(r"\.[0-9a-f]{32}\.mp3\.tmp")
VOICE_KEEP_DAYS = 30  # a voice file lives this long, then the housekeeping removes it
TEMP_KEEP = timedelta(days=1)  # a temp file a crash left behind
EXPIRE_MAX = 500  # files removed per pass — a large backlog is cleared over several passes
VOICE_MAX_CHARS = 3000  # a line's text for the voice — the tightest ElevenLabs request limit of her models (v3)
TTS_TIMEOUT_S = 60.0  # one generation on the server, at most — every later outbox write waits behind it (review #1)
VOICE_UNAVAILABLE = "voice unavailable: no ElevenLabs key or voice id"
_SENTENCE_END = ".!?…"


class VoiceUnavailable(Exception):
    """The switch can't be turned: the server has no ElevenLabs key or voice id."""


def voice_text(text: str, limit: int = VOICE_MAX_CHARS) -> str:
    """The text a line is voiced from, at most ``limit`` chars — cut at the last sentence end in the second half of
    the window, else at the last space, else hard."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(c) for c in _SENTENCE_END)
    if end >= limit // 2:
        return cut[: end + 1]
    space = cut.rfind(" ")
    return cut[:space].rstrip() if space > 0 else cut


@dataclass(frozen=True)
class VoiceSetup:
    """What the server needs to voice her lines: the synthesizer (``None`` without a key and a voice id), the store's
    root (``<data root>/voice``), the switch's file (``<data root>/voice.json``) and its first value
    (``telegram.voice``)."""

    tts: Any
    root: Path
    switch_path: Path
    first: bool = False
    ledger: Path | None = None  # v5.1: <data root>/media-ledger.jsonl — one `tts` record per generation


class VoiceSwitch:
    """The one «Voice» switch: persisted in ``voice.json``; ``active`` only while it is on and a voice is available."""

    def __init__(self, path: Path | None, *, first: bool, available: bool) -> None:
        self._path = path
        self._first = bool(first)
        self.available = bool(available)
        self._lock = threading.Lock()
        self._enabled = self._load()

    def _load(self) -> bool:
        if self._path is None or not self._path.is_file():
            return self._first
        try:
            value = json.loads(self._path.read_text(encoding="utf-8")).get("enabled")
        except (OSError, ValueError, AttributeError):
            value = None
        if isinstance(value, bool):
            return value
        log.warning("voice.json can't be read — the switch starts from telegram.voice (%s)", self._first)
        return self._first

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def active(self) -> bool:
        return self._enabled and self.available

    def set(self, enabled: bool) -> dict[str, bool]:
        """Turn it on or off and persist it (atomically). Refused while no voice is available — nothing written."""
        if not self.available:
            raise VoiceUnavailable(VOICE_UNAVAILABLE)
        with self._lock:
            if self._path is not None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._path.with_name(self._path.name + ".tmp")
                tmp.write_text(json.dumps({"enabled": bool(enabled)}), encoding="utf-8")
                tmp.replace(self._path)
            self._enabled = bool(enabled)
        return self.view()

    def view(self) -> dict[str, bool]:
        """What a client sees: ``{enabled, available}`` — ``enabled`` is whether her lines are voiced now."""
        return {"enabled": self.active, "available": self.available}


class VoiceStore:
    """Her voice files, a folder per user: ``<root>/<user_id>/<id>.mp3``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def path_for(self, user_id: str, voice_id: str) -> Path:
        """The file of one line. Refuses an id that isn't 32 lowercase hex, and a user id that isn't one plain name."""
        if not isinstance(voice_id, str) or not VOICE_ID_RE.fullmatch(voice_id):
            raise ValueError("not a voice id")
        uid = str(user_id or "")
        if not uid or uid.startswith(".") or any(c in uid for c in "/\\\0"):
            raise ValueError("not a user id")
        return self.root / uid / f"{voice_id}.mp3"

    def write(self, user_id: str, voice_id: str, data: bytes) -> Path:
        """Store one line's audio: a temp file, then a rename — a reader never sees half a file."""
        path = self.path_for(user_id, voice_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{voice_id}.mp3.tmp")
        try:
            tmp.write_bytes(data)
            tmp.replace(path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return path

    def read(self, user_id: str, voice_id: str) -> bytes | None:
        """One line's audio, or ``None`` (a bad id, a link, missing, expired or unreadable)."""
        try:
            path = self.path_for(user_id, voice_id)
        except ValueError:
            return None
        if path.is_symlink() or not path.is_file():
            return None
        try:
            return path.read_bytes()
        except OSError:
            return None

    def expire(self, now: datetime, *, keep_days: int = VOICE_KEEP_DAYS, limit: int = EXPIRE_MAX) -> int:
        """Remove the voice files older than ``keep_days`` (and temp files older than a day), at most ``limit`` per
        pass; returns how many. Only plain files named like a voice file, one folder below the root — never a link,
        never anything outside the voice folder."""
        cutoff = (now - timedelta(days=keep_days)).timestamp()
        temp_cutoff = (now - TEMP_KEEP).timestamp()
        removed = 0
        try:
            users = [e for e in os.scandir(self.root) if e.is_dir(follow_symlinks=False)]
        except OSError:
            return 0
        for user in users:
            try:
                entries = list(os.scandir(user.path))
            except OSError:
                continue
            for entry in entries:
                if removed >= limit:
                    return removed
                if not entry.is_file(follow_symlinks=False):
                    continue
                if VOICE_FILE_RE.fullmatch(entry.name):
                    before = cutoff
                elif _TEMP_RE.fullmatch(entry.name):
                    before = temp_cutoff
                else:
                    continue
                try:
                    if entry.stat(follow_symlinks=False).st_mtime < before:
                        os.unlink(entry.path)
                        removed += 1
                except OSError:
                    log.warning("voice expiry: %s could not be removed", entry.name)
        return removed


_Write = Callable[..., Any]
AUTO = object()  # LineVoicer.line: take a voice id now (the switch decides)


class LineVoicer:
    """The server's one ordered outbox writer, and her voice — see the module doc.

    ``write(fn, *args)`` appends ``fn(outbox, *args)``: at once when nothing is queued ahead of it, else after what is.
    ``line(fn, *args, text=, emotion=)`` is her line: while the switch is on it returns the line's voice id and queues
    the generation; ``fn(outbox, *args, voice=...)`` is written once the audio is ready (or failed). ``on_ready(id,
    ok)`` is called after each generation."""

    def __init__(self, outbox: Path | None, *, switch: VoiceSwitch | None = None, store: VoiceStore | None = None,
                 tts: Any = None, user_id: str = "owner", on_ready: Callable[[str, bool], None] | None = None,
                 max_chars: int = VOICE_MAX_CHARS, timer: Callable[[], float] = time.monotonic,
                 ledger: Path | None = None) -> None:
        self.outbox = outbox
        self.switch = switch
        self.store = store
        self.tts = tts
        self.user_id = str(user_id)
        self.on_ready = on_ready
        self.max_chars = max_chars
        self._timer = timer
        self.ledger = ledger  # v5.1: the media ledger, or None (not recorded)
        self._origins: dict[str, usage_origin.UsageOrigin] = {}  # voice id → the origin captured at queue time
        self._queue: deque[tuple[Any, ...]] = deque()
        self._cv = threading.Condition()
        self._busy = False
        self._closed = False
        self._current: tuple[Any, ...] | None = None  # the job the worker is on, until its record is written
        self._abandoned: set[str] = set()  # lines close() wrote unvoiced while they were still being voiced
        self._thread: threading.Thread | None = None

    @property
    def active(self) -> bool:
        """Whether her next line is voiced: the switch is on, a voice is available, and the server can make it."""
        return bool(self.switch is not None and self.switch.active and self.store is not None and self.tts is not None)

    # --- the queue --------------------------------------------------------------------------------------------
    def write(self, fn: _Write, *args: Any, **fields: Any) -> None:
        """One outbox write in its turn. Run in the caller's thread when nothing is ahead of it — holding the queue's
        lock, so nothing else appends meanwhile; queued behind a line still being voiced otherwise."""
        with self._cv:
            self.write_locked(fn, args, fields)

    def new_id(self) -> str | None:
        """A voice id for her next line while the switch is on, else ``None`` — taken before the line is shown, so its
        ``message`` push names the id and the ``voice`` push always comes after it."""
        return uuid.uuid4().hex if self.active else None

    def line(self, fn: _Write, *args: Any, text: str, emotion: str | None, voice_id: Any = AUTO) -> str | None:
        """Her line's outbox record and, with a voice id, its voice. ``voice_id`` from :meth:`new_id` (``AUTO``: take
        one now). Returns the voice id, or ``None`` (the switch off — the record is written as any other, with
        ``voice: null``)."""
        if voice_id is AUTO:
            voice_id = self.new_id()
        if voice_id is None:
            self.write(fn, *args, voice=None)
            return None
        with self._cv:
            if self.tts is None or self.store is None:  # nothing can voice it: the text goes, the line is told
                self.write_locked(fn, args, {"voice": None})
                self._report(voice_id, False)
                return voice_id
            self._origins[voice_id] = usage_origin.current()  # the worker thread won't see the turn's origin
            self._enqueue(("line", fn, args, voice_id, text, emotion))
        return voice_id

    def write_locked(self, fn: _Write, args: tuple[Any, ...], fields: dict[str, Any]) -> None:
        """``write`` for a caller that holds the queue's lock."""
        if self._queue or self._busy:
            self._enqueue(("write", fn, args, fields))
        else:
            self._append(fn, args, fields)

    def _enqueue(self, job: tuple[Any, ...]) -> None:  # the caller holds self._cv
        if self._closed:  # stopping: the write still happens, in the caller's thread, unvoiced
            self._unvoiced(job)
            return
        self._queue.append(job)
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="lumi-voice")
            self._thread.start()
        self._cv.notify_all()

    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._queue and not self._closed:
                    self._cv.wait()
                if not self._queue:
                    return
                job = self._queue.popleft()
                self._busy = True
                self._current = job
            try:
                if job[0] == "line":
                    self._voice(*job[1:])
                else:
                    self._append(job[1], job[2], job[3])
            except Exception:  # noqa: BLE001 — one bad job never stops the writer
                log.exception("voice: a queued write failed")
            finally:
                with self._cv:
                    self._busy = False
                    self._current = None
                    self._cv.notify_all()

    def drain(self, timeout: float | None = None) -> bool:
        """Wait until everything queued is written. ``False`` if ``timeout`` passed first."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cv:
            while self._queue or self._busy:
                left = None if deadline is None else deadline - time.monotonic()
                if left is not None and left <= 0:
                    return False
                self._cv.wait(left)
        return True

    def close(self, timeout: float = 30.0) -> None:
        """Finish what is queued (bounded by ``timeout``), then stop the worker; later writes run in the caller.

        Review #1: what the drain didn't finish is written now, in the caller — the line still being voiced and
        everything queued behind it, in order, unvoiced (``voice: null``) — so a stop never loses a line; the worker
        drops its late record for a line written here."""
        self.drain(timeout)
        with self._cv:
            self._closed = True
            late: list[tuple[Any, ...]] = []
            if self._busy and self._current is not None and self._current[0] == "line":
                late.append(self._current)
                self._abandoned.add(self._current[3])
            late += self._queue
            self._queue.clear()
            for job in late:
                self._unvoiced(job)
            self._cv.notify_all()
            thread = self._thread
        if late:
            log.warning("voice: stopping — %d outbox write(s) not finished in %.0f s, written now without a voice",
                        len(late), timeout)
        if thread is not None:
            thread.join(1.0)

    def _unvoiced(self, job: tuple[Any, ...]) -> None:  # the caller holds self._cv
        """One job written at once, in the caller — a line without its voice (and told so)."""
        if job[0] == "line":
            _, fn, args, voice_id, _, _ = job
            self._origins.pop(voice_id, None)  # never voiced: nothing to record
            self._append(fn, args, {"voice": None})
            self._report(voice_id, False)
        else:
            _, fn, args, fields = job
            self._append(fn, args, fields)

    # --- the work ---------------------------------------------------------------------------------------------
    def _append(self, fn: _Write, args: tuple[Any, ...], fields: dict[str, Any]) -> None:
        """One append — best-effort: a full disk never fails a turn."""
        if self.outbox is None:
            return
        try:
            fn(self.outbox, *args, **fields)
        except OSError:
            log.warning("outbox write failed", exc_info=True)

    def _voice(self, fn: _Write, args: tuple[Any, ...], voice_id: str, text: str, emotion: str | None) -> None:
        spoken = voice_text(text, self.max_chars)
        started = self._timer()
        ok = False
        origin = self._origins.pop(voice_id, None)
        try:
            if not spoken:
                raise ValueError("nothing to voice")
            try:
                audio = self.tts.synth(spoken, emotion=emotion)
            except Exception as exc:
                self._record(spoken, started, ledgers.error_outcome(exc), origin)
                raise
            self._record(spoken, started, "ok", origin)
            if not audio:
                raise ValueError("no audio")
            self.store.write(self.user_id, voice_id, bytes(audio))
            ok = True
            log.info("voice: made %s (%d chars, %d ms)", voice_id, len(spoken), int((self._timer() - started) * 1000))
        except Exception as exc:  # noqa: BLE001 — a failed generation degrades to text; never the text in the log
            log.warning("voice: failed %s (%s: %s)", voice_id, type(exc).__name__, str(exc)[:200])
        with self._cv:  # the record and close() never both write this line
            if voice_id in self._abandoned:  # the server stopped meanwhile: close() wrote it, unvoiced
                self._abandoned.discard(voice_id)
                return
            self._current = None
            self._append(fn, args, {"voice": f"{voice_id}.mp3" if ok else None})
        self._report(voice_id, ok)

    def _record(self, spoken: str, started: float, outcome: str, origin: usage_origin.UsageOrigin | None) -> None:
        """v5.1: one ``tts`` record for the call just made — the characters voiced, never the text. A line pushed
        with no work around it (no origin) is housekeeping."""
        if self.ledger is None:
            return
        origin = origin or usage_origin.UsageOrigin()
        ledgers.append_media(self.ledger, ledgers.media_record(
            provider="elevenlabs", service="tts", model=str(getattr(self.tts, "model", "") or ""),
            units={"chars": len(spoken)}, latency_ms=int((self._timer() - started) * 1000), outcome=outcome,
            origin=origin.origin or usage_origin.housekeeping("voice"), channel=origin.channel))

    def _report(self, voice_id: str, ok: bool) -> None:
        if self.on_ready is None:
            return
        try:
            self.on_ready(voice_id, ok)
        except Exception:  # noqa: BLE001 — a failed push never stops the writer
            log.warning("voice: reporting %s failed", voice_id, exc_info=True)

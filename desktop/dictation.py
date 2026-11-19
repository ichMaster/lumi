"""Dictation in the window (v4.7 LUMI-333) — the shell's side: the Mac's mic → the server → the page.

The page can't record (no WebKit microphone permission is involved): the window's Python shell opens the mic with
``sounddevice`` (16 kHz mono int16, the dictator's format, on ``dictation.stt_device``), streams it to the server's
``/v1/dictation`` WebSocket with the client token, and hands the server's answers to the page through the proxy's
local routes (``/local/dictation/*``, behind the window's key).

:class:`Dictation` runs one dictation at a time:

* :meth:`claim` takes the slot (:class:`Busy` while one runs); :meth:`run` connects, waits for the server's
  ``ready``, **then** opens the mic; each block goes to the socket and gives a ``level`` event (0..1, at most about
  ten a second); the server's ``interim`` / ``final`` / ``error`` / ``done`` are passed on as they come;
* :meth:`stop` closes the mic at once and tells the server (``stop``) after the last block; the run ends on ``done``
  or ``error``. :meth:`cancel` closes the mic and the socket and passes nothing more on. :meth:`close` (the window
  closing) cancels.

The mic is closed on every way out — a stop, a cancel, an error, the 5-minute cap, the window's close. No audio is
kept: a block goes from the mic to the socket and nowhere else. ``open_mic`` and ``connect`` are injectable, so
tests run with a fake mic and a fake socket.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import queue
import threading
import time
from array import array
from collections.abc import Callable
from typing import Any

log = logging.getLogger("lumi.desktop.dictation")

RATE = 16_000
BLOCK = 1_600  # 100 ms of audio per block
MAX_S = 300.0  # a recording is at most 5 minutes (the server caps it too)
LEVEL_EVERY_S = 0.1  # the level meter's pace
READY_S = 10.0  # how long the server gets to answer ``ready``
DONE_S = 60.0  # after a stop, how long the server gets to finish (a whole recording recognized at once)
_STOP = json.dumps({"type": "stop"})
_CANCEL = json.dumps({"type": "cancel"})
_END = object()

Emit = Callable[[dict[str, Any]], None]


class Busy(Exception):
    """A dictation is already running in this window."""


def level_of(block: bytes) -> float:
    """The block's peak, 0..1 — what the level meter shows."""
    samples = array("h")
    samples.frombytes(block[: len(block) - len(block) % 2])
    return min(1.0, max((abs(s) for s in samples), default=0) / 32768)


def ws_url(http_url: str) -> str:
    """``http://host:port`` → ``ws://host:port/v1/dictation`` (``https`` → ``wss``)."""
    base = http_url.rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base[len("https://"):] + "/v1/dictation"
    return "ws://" + base.removeprefix("http://") + "/v1/dictation"


def _open_mic(device: str, on_block: Callable[[bytes], None]) -> Any:  # pragma: no cover - the Mac's mic
    import sounddevice as sd

    from voice.dictator import resolve_input_device

    index = resolve_input_device(device, list(sd.query_devices()))
    stream = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK, device=index,
                               callback=lambda indata, frames, when, status: on_block(bytes(indata)))
    stream.start()

    class _Mic:
        def close(self) -> None:
            stream.stop()
            stream.close()

    return _Mic()


def _connect(url: str, token: str) -> Any:  # pragma: no cover - the network
    from websockets.sync.client import connect

    return connect(url, additional_headers={"Authorization": f"Bearer {token}"}, open_timeout=READY_S,
                   max_size=1 << 20, compression=None)


class Dictation:
    """One window's dictation — see the module doc."""

    def __init__(self, upstream: Any, *, device: str = "", open_mic: Callable[..., Any] | None = None,
                 connect: Callable[[str, str], Any] | None = None, clock: Callable[[], float] = time.monotonic,
                 max_s: float = MAX_S, level_every: float = LEVEL_EVERY_S, done_s: float = DONE_S) -> None:
        self.upstream = upstream
        self.device = device
        self._open_mic = open_mic or _open_mic
        self._connect = connect or _connect
        # what the real mic and socket need installed (an injected one needs nothing)
        self._needs = tuple(module for module, given in (("sounddevice", open_mic), ("websockets", connect))
                            if given is None)
        self._clock = clock
        self.max_bytes = int(max_s * RATE * 2)
        self.level_every = level_every
        self.done_s = done_s
        self._lock = threading.Lock()
        self._claimed = False
        self._mic: Any = None
        self._ws: Any = None
        self._blocks: queue.Queue = queue.Queue()
        self._stopped = False
        self._cancelled = False
        self._stopped_at: float | None = None

    # --- what the page asks -----------------------------------------------------------------------------------
    def available(self) -> tuple[bool, str]:
        """Whether this window can record, and why not."""
        if not getattr(self.upstream, "configured", False):
            return False, "no server is set"
        for module in self._needs:
            if importlib.util.find_spec(module) is None:
                return False, f"{module} is not installed"
        return True, ""

    @property
    def running(self) -> bool:
        with self._lock:
            return self._claimed

    def claim(self) -> None:
        """Take the one dictation slot (before the page's stream starts) — :class:`Busy` while one runs."""
        with self._lock:
            if self._claimed:
                raise Busy("a dictation is already running")
            self._claimed = True
            self._stopped = self._cancelled = False
            self._stopped_at = None
            self._blocks = queue.Queue()

    def stop(self) -> bool:
        """End the recording: the mic closes now, the server hears ``stop`` after the last block. ``False`` when
        nothing runs."""
        with self._lock:
            if not self._claimed or self._stopped or self._cancelled:
                return False
            self._stopped = True
            self._stopped_at = self._clock()
        self._close_mic()
        self._blocks.put(_END)
        return True

    def cancel(self) -> bool:
        """Drop the recording: the mic and the socket close, nothing more reaches the page."""
        with self._lock:
            if not self._claimed or self._cancelled:
                return False
            self._cancelled = True
        self._close_mic()
        self._blocks.put(_END)
        ws = self._ws
        if ws is not None:
            try:
                ws.send(_CANCEL)
            except Exception:  # noqa: BLE001 — the socket is going anyway
                pass
            self._close_ws(ws)
        return True

    def close(self) -> None:
        """The window is closing: a running dictation is cancelled, so the mic is closed."""
        self.cancel()

    # --- one dictation ----------------------------------------------------------------------------------------
    def run(self, emit: Emit) -> str:
        """Run the claimed dictation to its end, passing every event to ``emit``; returns how it ended (``done``,
        ``error``, ``cancel``). Blocks — the proxy's stream thread calls it."""
        sink = _Sink(emit, lambda: self._cancelled, self.cancel)
        outcome = "error"
        sender: threading.Thread | None = None
        try:
            try:
                self._ws = self._connect(ws_url(self.upstream.url), self.upstream.token)
            except Exception as exc:  # noqa: BLE001 — the server down, refused, a wrong token
                log.warning("dictation: the server can't be reached (%s)", type(exc).__name__)
                sink({"type": "error", "detail": f"the server can't be reached ({type(exc).__name__})"})
                return outcome
            ready = self._recv(READY_S)
            if not isinstance(ready, dict) or ready.get("type") != "ready":
                if isinstance(ready, dict) and ready.get("type") == "error":
                    sink(ready)
                elif not self._cancelled:
                    sink({"type": "error", "detail": "the server didn't start the dictation"})
                return "cancel" if self._cancelled else outcome
            sink(ready)
            if self._cancelled:
                return "cancel"
            if not self._stopped:  # the mic opens only now — after the server is ready, never before
                try:
                    mic = self._open_mic(self.device, self._on_block)
                except Exception as exc:  # noqa: BLE001 — no device, no permission
                    sink({"type": "error", "detail": f"the microphone can't be opened ({type(exc).__name__})"})
                    self._send(_CANCEL)
                    return outcome
                with self._lock:
                    self._mic = mic
                    late = self._stopped or self._cancelled
                if late:  # stopped or cancelled while the mic was opening
                    self._close_mic()
                if self._cancelled:
                    return "cancel"
            sender = threading.Thread(target=self._send_blocks, args=(sink,), daemon=True,
                                      name="lumi-dictation-send")
            sender.start()
            outcome = self._receive(sink)
            return outcome
        finally:
            self._close_mic()
            self._blocks.put(_END)
            if sender is not None:
                sender.join(2.0)
            if self._ws is not None:
                self._close_ws(self._ws)
                self._ws = None
            with self._lock:
                self._claimed = False
            if self._cancelled:
                outcome = "cancel"
            log.info("dictation: %s", outcome)

    def _on_block(self, block: bytes) -> None:  # the mic's thread: hand the block on, never block here
        if not (self._stopped or self._cancelled):
            self._blocks.put(block)

    def _send_blocks(self, sink: _Sink) -> None:
        """The blocks in order to the socket, the level to the page, the cap; ``stop`` after the last block."""
        sent = 0
        last_level = None
        while True:
            block = self._blocks.get()
            if block is _END:
                break
            take = block[: max(0, self.max_bytes - sent)]
            if take and not self._send(take):
                return
            sent += len(take)
            now = self._clock()
            if last_level is None or now - last_level >= self.level_every:
                last_level = now
                sink({"type": "level", "value": round(level_of(take), 3)})
            if sent >= self.max_bytes:  # the 5-minute cap: a stop
                self.stop()
        if not self._cancelled:
            self._send(_STOP)

    def _receive(self, sink: _Sink) -> str:
        """The server's answers until ``done`` / ``error`` (or the socket closes)."""
        while True:
            if self._cancelled:
                return "cancel"
            if self._stopped_at is not None and self._clock() - self._stopped_at > self.done_s:
                sink({"type": "error", "detail": "the server didn't finish the dictation"})
                return "error"
            message = self._recv(1.0)
            if message is None:
                continue
            if message is _END:
                if not self._cancelled:
                    sink({"type": "error", "detail": "the connection to the server broke"})
                return "cancel" if self._cancelled else "error"
            kind = message.get("type")
            if kind in ("interim", "final", "error", "done"):
                sink(message)
            if kind in ("done", "error"):
                return kind

    def _recv(self, timeout: float) -> Any:
        """The server's next message as a dict; ``None`` on a timeout; ``_END`` once the socket is closed."""
        ws = self._ws
        if ws is None:
            return _END
        try:
            raw = ws.recv(timeout=timeout)
        except TimeoutError:
            return None
        except Exception:  # noqa: BLE001 — the socket closed or broke
            return _END
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _send(self, data: bytes | str) -> bool:
        ws = self._ws
        if ws is None:
            return False
        try:
            ws.send(data)
            return True
        except Exception:  # noqa: BLE001 — the socket closed: the receiving side reports it
            return False

    def _close_mic(self) -> None:
        with self._lock:
            mic, self._mic = self._mic, None
        if mic is not None:
            try:
                mic.close()
            except Exception:  # noqa: BLE001 — closing a device that's already gone
                log.warning("dictation: the microphone didn't close cleanly", exc_info=True)

    @staticmethod
    def _close_ws(ws: Any) -> None:
        try:
            ws.close()
        except Exception:  # noqa: BLE001 — already closed
            pass


class _Sink:
    """``emit`` from several threads, one at a time; silent once the dictation is cancelled or the page left."""

    def __init__(self, emit: Emit, cancelled: Callable[[], bool], on_gone: Callable[[], Any]) -> None:
        self._emit = emit
        self._cancelled = cancelled
        self._on_gone = on_gone
        self._lock = threading.Lock()
        self.gone = False

    def __call__(self, message: dict[str, Any]) -> None:
        with self._lock:
            if self.gone or self._cancelled():
                return
            try:
                self._emit(message)
            except OSError:  # the page went away (a reload): the dictation is cancelled, the mic closed
                self.gone = True
                self._on_gone()

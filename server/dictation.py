"""Dictation in the window (v4.7 LUMI-331) — the server's half: the owner's audio in, the words out.

The window's shell records the Mac's mic and streams it to ``/v1/dictation``; :func:`relay` recognizes it with the
box's key and answers in small JSON messages the window shows in its input:

* ``{"type": "ready", "mode": "stream" | "batch"}`` — first;
* ``{"type": "interim", "text"}`` — the phrase being spoken, replaced by the next one (stream mode);
* ``{"type": "final", "text"}`` — a settled phrase, to append to the input;
* ``{"type": "error", "detail"}`` — one readable line, then the end (the window leaves its input as it was);
* ``{"type": "done"}`` — after a stop, once the last ``final`` is out.

The client sends PCM (16 kHz mono int16) as binary frames and ``{"type": "stop"}`` / ``{"type": "cancel"}`` as text.
**Stream mode** feeds the v1.6 :class:`~voice.stream_stt.DeepgramStream` and its phrase hold
(:class:`~voice.stream_stt.UtteranceTracker`); **batch mode** (no streaming recognizer, or it can't open) keeps the
audio in memory and recognizes it whole on the stop with the batch adapter (``voice/stt.py``), as the TUI's dictator
does. Nothing is written to disk; the log gets one line per dictation — the mode, the seconds, the characters, how
it ended — never the words.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from voice.stream_stt import MIC_RATE, UtteranceTracker
from voice.stt import wav_bytes

log = logging.getLogger("lumi.dictation")

MAX_S = 300.0  # a recording is at most 5 minutes — then it is treated as a stop
IDLE_S = 10.0  # no audio frame for this long → the dictation ends with an error
FINISH_S = 3.0  # after a stop, how long the streaming recognizer gets to send its last results
BYTES_PER_S = MIC_RATE * 2  # 16 kHz mono int16

Receive = Callable[[], Awaitable["bytes | str | None"]]  # None: the client went away
Send = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class DictationSetup:
    """What the server recognizes with: the batch recognizer (``voice.stt.build_stt``), a factory opening the
    streaming one (``None`` → batch only: not Deepgram, no key, no ``websockets``) and the language (``stt_lang``)."""

    stt: Any
    stream_factory: Callable[[], Awaitable[Any]] | None = None
    lang: str = "uk"


class _Gone(Exception):
    """The client went away while the relay was answering."""


async def _cancelled(task: asyncio.Future) -> None:
    """Cancel a task of the relay's own and wait for it to end."""
    if task.done():
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001 — its own end
        pass


def _transcript(event: dict) -> str:
    alt = (((event.get("channel") or {}).get("alternatives")) or [{}])[0]
    return (alt.get("transcript") or "").strip()


def _control(message: str) -> str:
    try:
        data = json.loads(message)
    except (ValueError, TypeError):
        return ""
    return str(data.get("type") or "") if isinstance(data, dict) else ""


class _Run:
    """One dictation's state: the words said so far, the audio (batch mode only), the counts for the log line."""

    def __init__(self, send: Send, setup: DictationSetup, stream: Any, max_bytes: int) -> None:
        self._send = send
        self.setup = setup
        self.stream = stream
        self.max_bytes = max_bytes
        self.tracker = UtteranceTracker()
        self.shown = ""
        self.chars = 0
        self.audio = 0
        self.pcm = bytearray()

    async def send(self, message: dict[str, Any]) -> None:
        try:
            await self._send(message)
        except Exception as exc:  # noqa: BLE001 — a closed socket: the client is gone
            raise _Gone from exc

    async def interim(self, text: str) -> None:
        text = " ".join(text.split())
        if text != self.shown:
            self.shown = text
            await self.send({"type": "interim", "text": text})

    async def final(self, text: str | None) -> None:
        text = (text or "").strip()
        if text:
            self.chars += len(text)
            self.shown = ""
            await self.send({"type": "final", "text": text})

    async def error(self, detail: str) -> str:
        await self.send({"type": "error", "detail": detail})
        return "error"

    async def events(self) -> None:
        """Stream mode: the recognizer's events → ``interim`` / ``final`` (the phrase hold kept)."""
        async for event in self.stream.events():
            if event.get("type") == "Results" and not event.get("is_final"):
                await self.interim(f"{self.tracker.pending()} {_transcript(event)}")
                continue
            said = self.tracker.feed(event)
            if said:
                await self.final(said)
            elif event.get("type") == "Results":
                await self.interim(self.tracker.pending())

    async def frame(self, data: bytes) -> bool:
        """One audio frame; ``True`` once the recording reached its cap."""
        take = data[: max(0, self.max_bytes - self.audio)]
        self.audio += len(take)
        if self.stream is not None:
            await self.stream.send_pcm(take)
        else:
            self.pcm += take
        return self.audio >= self.max_bytes

    async def finish(self, events: asyncio.Task | None, finish_s: float) -> str:
        """The stop: the last words, then ``done`` — or one ``error``."""
        try:
            if self.stream is not None:
                await self.stream.finish()
                if events is not None and not events.done():
                    await asyncio.wait({events}, timeout=finish_s)
                if events is not None and not events.done():  # the last results never came: end with what's here
                    await _cancelled(events)
                if events is not None and events.done() and not events.cancelled():
                    exc = events.exception()
                    if isinstance(exc, _Gone):
                        raise exc
                    if exc is not None:  # the stream broke while ending: the words so far still count
                        log.warning("dictation: the recognizer failed at the stop (%s)", type(exc).__name__)
                await self.final(self.tracker.flush())
            elif self.pcm:
                audio = wav_bytes(bytes(self.pcm), MIC_RATE)
                self.pcm = bytearray()
                try:
                    text = await asyncio.to_thread(self.setup.stt.recognize, audio, lang=self.setup.lang)
                except Exception as exc:  # noqa: BLE001 — the type only: a message could quote the audio's words
                    return await self.error(f"recognition failed ({type(exc).__name__})")
                await self.final(text)
            await self.send({"type": "done"})
            return "done"
        finally:
            self.pcm = bytearray()


async def relay(receive: Receive, send: Send, setup: DictationSetup, *, clock: Callable[[], float] = time.monotonic,
                max_s: float = MAX_S, idle_s: float = IDLE_S, finish_s: float = FINISH_S) -> str:
    """One dictation, start to end; returns how it ended — ``done``, ``cancel``, ``error`` or ``gone``.

    ``receive()`` gives the client's next frame (bytes: audio; str: a control message; ``None``: the client left);
    ``send(message)`` answers. The caps: ``max_s`` of audio (or of time) is a stop, ``idle_s`` without an audio frame
    is an error."""
    started = clock()
    stream = None
    if setup.stream_factory is not None:
        try:
            stream = await setup.stream_factory()
        except Exception as exc:  # noqa: BLE001 — the provider down, no websockets: recognize it whole instead
            log.warning("dictation: the streaming recognizer can't open (%s) — recognized whole on the stop",
                        type(exc).__name__)
            stream = None
    mode = "stream" if stream is not None else "batch"
    run = _Run(send, setup, stream, max_bytes=int(max_s * BYTES_PER_S))
    loop = asyncio.get_running_loop()
    events: asyncio.Task | None = None
    recv: asyncio.Future | None = None
    outcome = "gone"
    try:
        await run.send({"type": "ready", "mode": mode})
        if stream is not None:
            events = asyncio.ensure_future(run.events())
        last_audio = loop.time()
        recv = asyncio.ensure_future(receive())
        while True:
            waiting = {recv} | ({events} if events is not None else set())
            left = idle_s - (loop.time() - last_audio)
            done, _ = await asyncio.wait(waiting, timeout=max(0.0, left), return_when=asyncio.FIRST_COMPLETED)
            if not done:
                outcome = await run.error(f"no audio for {idle_s:.0f} s")
                break
            if events is not None and events in done:  # the recognizer ended before the stop
                exc = events.exception()
                if isinstance(exc, _Gone):
                    raise exc
                detail = f"the recognizer stopped ({type(exc).__name__})" if exc else "the recognizer stopped"
                outcome = await run.error(detail)
                break
            if recv not in done:
                continue
            try:
                message = recv.result()
            except Exception:  # noqa: BLE001 — a dropped connection reads as the client leaving
                message = None
            recv = None
            if message is None:
                outcome = "gone"
                break
            if isinstance(message, (bytes, bytearray)):
                last_audio = loop.time()
                if await run.frame(bytes(message)) or clock() - started >= max_s:
                    outcome = await run.finish(events, finish_s)
                    break
            else:
                kind = _control(message)
                if kind == "stop":
                    outcome = await run.finish(events, finish_s)
                    break
                if kind == "cancel":
                    outcome = "cancel"
                    break
            recv = asyncio.ensure_future(receive())
    except _Gone:
        outcome = "gone"
    finally:
        for task in (recv, events):
            if task is not None:
                await _cancelled(task)
        if stream is not None:
            try:
                await stream.close()
            except Exception:  # noqa: BLE001 — closing a dead socket
                pass
        run.pcm = bytearray()
        log.info("dictation: %s, %.1f s of audio, %d chars, %s", mode, run.audio / BYTES_PER_S, run.chars, outcome)
    return outcome

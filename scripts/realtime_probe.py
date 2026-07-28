"""v1.6.1 (LUMI-197) — the live voice probe: mic ↔ OpenAI Realtime ↔ speaker, pure context.

A standalone **manual, paid** dev script (the `gemini_probe` pattern — NEVER run in CI): a live
spoken conversation with Лілі on **`gpt-realtime-2.1-mini`**. No tools, no classifier, no
persistence — the session `instructions` are a one-shot snapshot of her assembled system prompt
(`Core.prompt_snapshot()`, LUMI-196). Wear **headphones** — there is no echo cancellation, the
model must not hear itself. Close the running TUI first (the store is opened read-only here, but a
concurrent writer would race the snapshot's lazy ensures).

Run:
    OPENAI_API_KEY=… uv run --extra realtime --extra embed python scripts/realtime_probe.py

Env: LUMI_REALTIME_MODEL (default gpt-realtime-2.1-mini) · LUMI_REALTIME_VOICE (default marin) ·
LUMI_STT_DEVICE (mic pick, as the dictator) · plus the normal Lumi env for the snapshot.

Per turn it prints: your transcript, her transcript, and the **speech-stop → first-audio latency**;
Ctrl+C prints the session summary (median/min/max) — the numbers LUMI-198 records into
VOICE_MODE.md. Unknown event types are collected and printed at exit (the event-shape discovery
v1.6.2's adapter builds on).

The helpers below (`build_session_update`, `ProbeDispatcher`, pcm base64 framing) are pure and
unit-tested without any network or audio; only `run()` touches the world.
"""

from __future__ import annotations

import base64
import json
import os
import statistics
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root on the path

DEFAULT_MODEL = "gpt-realtime-2.1-mini"
DEFAULT_VOICE = "marin"
SAMPLE_RATE = 24_000  # pcm16 mono @ 24 kHz, both directions

REALTIME_URL = "wss://api.openai.com/v1/realtime"


def build_session_update(instructions: str, *, voice: str = DEFAULT_VOICE) -> dict:
    """The ``session.update`` payload: pure context + semantic VAD + pcm16@24k, audio out.

    The voice MUST ride the first update — it is immutable after the session's first audio
    (the Realtime API rule). ``create_response`` stays default (true): the model answers directly —
    realtime-as-brain is exactly what this probe tastes."""
    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "instructions": instructions,
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                    "transcription": {"model": "gpt-realtime-whisper"},
                    "turn_detection": {
                        "type": "semantic_vad",
                        "eagerness": "medium",
                        "interrupt_response": True,
                    },
                },
                "output": {
                    "voice": voice,
                    "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                },
            },
        },
    }


def b64_pcm(chunk: bytes) -> str:
    """A mic pcm16 chunk → the ``input_audio_buffer.append`` audio payload."""
    return base64.b64encode(chunk).decode("ascii")


def pcm_from_b64(data: str) -> bytes:
    return base64.b64decode(data)


class ProbeDispatcher:
    """Route Realtime server events → callbacks + latency math. Pure — fed with dicts, timed by an
    injected ``now()`` clock, so every path unit-tests without a socket.

    Latency = VAD ``speech_stopped`` → the first audio delta of the following response. Event names
    vary across API generations (``response.audio.delta`` vs ``response.output_audio.delta``…) —
    both are handled, and every UNKNOWN type is collected in :attr:`unknown` for the v1.6.2 report.
    """

    _AUDIO_DELTA = ("response.output_audio.delta", "response.audio.delta")
    _AUDIO_TRANSCRIPT_DELTA = (
        "response.output_audio_transcript.delta", "response.audio_transcript.delta",
    )
    _AUDIO_TRANSCRIPT_DONE = (
        "response.output_audio_transcript.done", "response.audio_transcript.done",
    )
    _USER_TRANSCRIPT = ("conversation.item.input_audio_transcription.completed",)
    _KNOWN_QUIET = (  # expected chatter we don't act on
        "session.created", "session.updated", "input_audio_buffer.speech_started",
        "input_audio_buffer.speech_stopped", "input_audio_buffer.committed",
        "conversation.item.created", "conversation.item.added", "conversation.item.done",
        "response.created", "response.done", "response.output_item.added",
        "response.output_item.done", "response.content_part.added", "response.content_part.done",
        "rate_limits.updated", "error",
    )

    def __init__(
        self,
        *,
        now: Callable[[], float],
        on_audio: Callable[[bytes], None],
        on_user_transcript: Callable[[str], None],
        on_assistant_transcript: Callable[[str], None],
        on_barge_in: Callable[[], None],
        on_first_audio: Callable[[float], None] | None = None,
    ) -> None:
        self._now = now
        self._on_audio = on_audio
        self._on_user = on_user_transcript
        self._on_assistant = on_assistant_transcript
        self._on_barge_in = on_barge_in
        self._on_first_audio = on_first_audio
        self._speech_stopped_at: float | None = None
        self._first_audio_pending = False
        self._playing = False
        self._assistant_parts: list[str] = []
        self.latencies: list[float] = []
        self.unknown: list[str] = []
        self.errors: list[dict] = []

    def feed(self, event: dict) -> None:
        etype = event.get("type", "")
        if etype == "input_audio_buffer.speech_stopped":
            self._speech_stopped_at = self._now()
            self._first_audio_pending = True
        elif etype == "input_audio_buffer.speech_started":
            if self._playing:  # barge-in: he spoke over her → stop local playback
                self._playing = False
                self._on_barge_in()
        elif etype in self._AUDIO_DELTA:
            if self._first_audio_pending and self._speech_stopped_at is not None:
                latency = self._now() - self._speech_stopped_at
                self.latencies.append(latency)
                self._first_audio_pending = False
                if self._on_first_audio is not None:
                    self._on_first_audio(latency)
            self._playing = True
            self._on_audio(pcm_from_b64(event.get("delta", "")))
        elif etype in self._AUDIO_TRANSCRIPT_DELTA:
            self._assistant_parts.append(event.get("delta", ""))
        elif etype in self._AUDIO_TRANSCRIPT_DONE:
            text = event.get("transcript") or "".join(self._assistant_parts)
            self._assistant_parts = []
            self._playing = False
            self._on_assistant(text)
        elif etype in self._USER_TRANSCRIPT:
            self._on_user(event.get("transcript", ""))
        elif etype == "error":
            self.errors.append(event.get("error", event))
        elif etype not in self._KNOWN_QUIET:
            if etype not in self.unknown:  # collect each shape once — the discovery output
                self.unknown.append(etype)

    def summary(self) -> dict:
        """The session latency summary (seconds) for the LUMI-198 write-back."""
        if not self.latencies:
            return {"turns": 0}
        return {
            "turns": len(self.latencies),
            "median_s": round(statistics.median(self.latencies), 2),
            "min_s": round(min(self.latencies), 2),
            "max_s": round(max(self.latencies), 2),
        }


def run() -> None:  # pragma: no cover — live WS + audio hardware glue (manual + paid, never CI)
    import asyncio
    import queue

    import sounddevice as sd
    import websockets

    from core.agent import build_core
    from core.config import load_config
    from voice.dictator import resolve_input_device

    cfg = load_config()  # loads .env first — OPENAI_API_KEY may live there
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set (.env or the environment) — the probe is a paid live call.")
    model = (os.getenv("LUMI_REALTIME_MODEL") or DEFAULT_MODEL).strip()
    voice = (os.getenv("LUMI_REALTIME_VOICE") or DEFAULT_VOICE).strip()

    print("assembling Лілі's prompt snapshot (close the TUI if it's running)…")
    core = build_core(config=cfg)
    session = core.start_session()
    instructions = core.prompt_snapshot(session)
    print(f"snapshot: {len(instructions)} chars · model={model} · voice={voice} · headphones ON?")

    speaker_q: queue.Queue[bytes] = queue.Queue()

    def on_audio(chunk: bytes) -> None:
        speaker_q.put(chunk)

    def drain_speaker() -> None:
        while not speaker_q.empty():
            try:
                speaker_q.get_nowait()
            except queue.Empty:
                break

    import time as _time
    dispatcher = ProbeDispatcher(
        now=_time.monotonic,
        on_audio=on_audio,
        on_user_transcript=lambda t: print(f"\nYou: {t}"),
        on_assistant_transcript=lambda t: print(f"Лілі: {t}"),
        on_barge_in=drain_speaker,
        on_first_audio=lambda s: print(f"  [first audio {s:.2f}s]"),
    )

    async def main() -> None:
        url = f"{REALTIME_URL}?model={model}"
        headers = {"Authorization": f"Bearer {api_key}"}
        async with websockets.connect(url, additional_headers=headers, max_size=1 << 24) as ws:
            await ws.send(json.dumps(build_session_update(instructions, voice=voice)))
            loop = asyncio.get_running_loop()

            device = resolve_input_device(cfg.stt_device, list(sd.query_devices()))

            def mic_cb(indata, frames, t, status) -> None:
                payload = json.dumps(
                    {"type": "input_audio_buffer.append", "audio": b64_pcm(bytes(indata))}
                )
                asyncio.run_coroutine_threadsafe(ws.send(payload), loop)

            def spk_cb(outdata, frames, t, status) -> None:
                need = len(outdata)
                buf = b""
                while len(buf) < need and not speaker_q.empty():
                    try:
                        buf += speaker_q.get_nowait()
                    except queue.Empty:
                        break
                buf = buf[:need].ljust(need, b"\x00")
                outdata[:] = buf

            mic = sd.RawInputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                    blocksize=2400, device=device, callback=mic_cb)
            spk = sd.RawOutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                     blocksize=2400, callback=spk_cb)
            with mic, spk:
                print("listening — говори з нею (Ctrl+C to stop)…")
                async for raw in ws:
                    dispatcher.feed(json.loads(raw))

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        print("\n── session summary ──")
        print(json.dumps(dispatcher.summary(), ensure_ascii=False))
        if dispatcher.unknown:
            print("unknown event types (record into VOICE_MODE.md):")
            for e in dispatcher.unknown:
                print(f"  - {e}")
        if dispatcher.errors:
            print("errors:")
            for e in dispatcher.errors:
                print(f"  - {e}")


if __name__ == "__main__":  # pragma: no cover
    run()

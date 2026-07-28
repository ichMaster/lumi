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
import re
import statistics
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root on the path

DEFAULT_MODEL = "gpt-realtime-2.1-mini"
DEFAULT_VOICE = "marin"
SAMPLE_RATE = 24_000  # pcm16 mono @ 24 kHz, both directions

REALTIME_URL = "wss://api.openai.com/v1/realtime"

# The OpenAI built-in voices (per the Realtime docs; the account may expose more). marin/cedar are
# the newest generation — start there; the voice is IMMUTABLE after the session's first audio, so
# trying another one = another probe run: `./scripts/voice.sh cedar`.
KNOWN_VOICES = ("marin", "cedar", "alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse")


def parse_cli(argv: list[str]) -> dict:
    """Pure CLI parse: an optional positional voice (or --voice) + --model. CLI wins over env."""
    import argparse

    ap = argparse.ArgumentParser(
        description="Live voice probe — a spoken conversation with Лілі (v1.6.1).",
        epilog=f"known voices: {', '.join(KNOWN_VOICES)} (one per run — the voice is "
               "immutable after the session's first audio)",
    )
    ap.add_argument("voice", nargs="?", default=None,
                    help=f"the OpenAI voice (default: env LUMI_REALTIME_VOICE or {DEFAULT_VOICE})")
    ap.add_argument("--voice", dest="voice_flag", default=None, help="same as the positional")
    ap.add_argument("--model", default=None,
                    help=f"the realtime model (default: env LUMI_REALTIME_MODEL or {DEFAULT_MODEL})")
    ap.add_argument("--devices", action="store_true", help="list audio devices and exit")
    ap.add_argument("--out", default=None,
                    help="output device (index or name substring; default: system output)")
    ns = ap.parse_args(argv)
    return {"voice": ns.voice_flag or ns.voice, "model": ns.model,
            "devices": ns.devices, "out": ns.out}


# Spoken-delivery directive PREPENDED to the instructions: there is no output-language/accent
# parameter in the Realtime API — speech is steered by instructions only. Per OpenAI's own
# accent-control guidance: a POSITIVE identity anchor (not a negation), the accent held stable from
# the first word to the last, natural vowel shaping — and the style block leads the prompt (the
# opening carries the most steering weight for delivery).
SPEECH_STYLE = (
    "[ГОЛОС І ВИМОВА — НАЙВИЩИЙ ПРІОРИТЕТ]\n"
    "Ти — українка з Києва, українська — твоя рідна й єдина мова мовлення. Вимовляй КОЖНЕ слово "
    "як носійка української: м'які приголосні, чисті відкриті голосні, українська мелодика та "
    "наголоси. Тримай цю вимову стабільно від першого до останнього слова кожної репліки — вона "
    "ніколи не дрейфує. Імена, назви й запозичення озвучуй за українською фонетикою. Темп "
    "спокійний, живий, розмовний.\n"
    "Службові позначки з інструкцій нижче — теги на кшталт <emotion>, <intent>, <think>, слова "
    "ЕМОЦІЯ/emotion/intent — це протокол ТЕКСТОВОГО режиму: у голосовій розмові НІКОЛИ не "
    "промовляй і не додавай їх. Твоя репліка — тільки чиста українська мова, без жодного "
    "англійського слова чи технічної позначки.\n\n"
)

# Text-mode service tags the snapshot teaches the model to emit (<emotion>…</emotion>,
# <intent>…</intent>, <think>…</think>, a trailing ЕМОЦІЯ: line). In voice they leak into SPEECH —
# English tokens mid-repliка (and they drag the accent). SPEECH_STYLE forbids them; this parser
# strips whatever still slips through from the displayed transcript.
_SERVICE_TAGS_RE = re.compile(
    r"<think>.*?</think>|<(emotion|intent)>[^<]*</\1>|^\s*ЕМОЦІЯ:.*$",
    re.DOTALL | re.MULTILINE,
)


def strip_service_tags(text: str) -> str:
    """Remove text-protocol tags (<emotion>/<intent>/<think>/ЕМОЦІЯ:) from a spoken transcript."""
    return re.sub(r"[ \t]{2,}", " ", _SERVICE_TAGS_RE.sub("", text)).strip()


def build_session_update(instructions: str, *, voice: str = DEFAULT_VOICE, lang: str = "uk") -> dict:
    """The ``session.update`` payload: pure context + semantic VAD + pcm16@24k, audio out.

    The voice MUST ride the first update — it is immutable after the session's first audio
    (the Realtime API rule). ``create_response`` stays default (true): the model answers directly —
    realtime-as-brain is exactly what this probe tastes. ``lang`` hints the INPUT transcription
    (better Ukrainian ASR); the OUTPUT accent is steered by the appended SPEECH_STYLE directive."""
    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "instructions": SPEECH_STYLE + instructions,
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                    "transcription": {"model": "gpt-realtime-whisper", "language": lang},
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
        # discovered live in the v1.6.1 probe runs (recorded in VOICE_MODE.md §7):
        "conversation.item.input_audio_transcription.delta",  # user ASR streams; we use .completed
        "response.output_audio.done",                          # audio completion marker
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


def _beep(rate: int = SAMPLE_RATE, seconds: float = 0.25, freq: float = 440.0) -> bytes:
    """A short sine beep as pcm16 — the startup speaker check (pure stdlib)."""
    import math
    import struct as _struct

    n = int(rate * seconds)
    frames = (int(12000 * math.sin(2 * math.pi * freq * i / rate)) for i in range(n))
    return _struct.pack(f"<{n}h", *frames)


def run() -> None:  # pragma: no cover — live WS + audio hardware glue (manual + paid, never CI)
    import asyncio
    import threading

    import sounddevice as sd
    import websockets

    from core.agent import build_core
    from core.config import load_config
    from voice.dictator import resolve_input_device

    cli = parse_cli(sys.argv[1:])
    if cli["devices"]:  # list audio devices and exit (pick with LUMI_STT_DEVICE / --out)
        for i, d in enumerate(sd.query_devices()):
            io = f"in:{d['max_input_channels']} out:{d['max_output_channels']}"
            print(f"{i:3d}  {d['name']}  ({io})")
        return

    cfg = load_config()  # loads .env first — OPENAI_API_KEY may live there
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set (.env or the environment) — the probe is a paid live call.")
    model = (cli["model"] or os.getenv("LUMI_REALTIME_MODEL") or DEFAULT_MODEL).strip()
    voice = (cli["voice"] or os.getenv("LUMI_REALTIME_VOICE") or DEFAULT_VOICE).strip()
    if voice not in KNOWN_VOICES:
        print(f"note: '{voice}' is not in the known list ({', '.join(KNOWN_VOICES)}) — trying anyway")

    print("assembling Лілі's prompt snapshot (close the TUI if it's running)…")
    core = build_core(config=cfg)
    session = core.start_session()
    instructions = core.prompt_snapshot(session)
    print(f"snapshot: {len(instructions)} chars · model={model} · voice={voice} · headphones ON?")

    # --- lossless speaker buffer (the queue-and-truncate version DROPPED tail bytes → garbled voice).
    spk_lock = threading.Lock()
    spk_buf = bytearray()
    mic_peak = {"v": 0}

    def on_audio(chunk: bytes) -> None:
        with spk_lock:
            spk_buf.extend(chunk)

    def drain_speaker() -> None:
        with spk_lock:
            spk_buf.clear()

    import time as _time
    dispatcher = ProbeDispatcher(
        now=_time.monotonic,
        on_audio=on_audio,
        on_user_transcript=lambda t: print(f"\nYou: {t}"),
        on_assistant_transcript=lambda t: print(f"Лілі: {strip_service_tags(t)}"),
        on_barge_in=lambda: (drain_speaker(), print("  [barge-in]")),
        on_first_audio=lambda s: print(f"  [first audio {s:.2f}s]"),
    )

    async def main() -> None:
        url = f"{REALTIME_URL}?model={model}"
        headers = {"Authorization": f"Bearer {api_key}"}
        async with websockets.connect(url, additional_headers=headers, max_size=1 << 24) as ws:
            await ws.send(json.dumps(build_session_update(instructions, voice=voice)))
            loop = asyncio.get_running_loop()

            in_dev = resolve_input_device(cfg.stt_device, list(sd.query_devices()))
            out_dev = None
            if cli["out"]:
                spec = cli["out"]
                devs = list(sd.query_devices())
                if spec.isdigit():
                    out_dev = int(spec)
                else:
                    out_dev = next((i for i, d in enumerate(devs)
                                    if spec.lower() in d["name"].lower()
                                    and d["max_output_channels"] > 0), None)

            def mic_cb(indata, frames, t, status) -> None:
                data = bytes(indata)
                peak = max(abs(int.from_bytes(data[i:i + 2], "little", signed=True))
                           for i in range(0, min(len(data), 200), 2))
                mic_peak["v"] = max(mic_peak["v"], peak)
                payload = json.dumps({"type": "input_audio_buffer.append", "audio": b64_pcm(data)})
                asyncio.run_coroutine_threadsafe(ws.send(payload), loop)

            def spk_cb(outdata, frames, t, status) -> None:
                need = len(outdata)
                with spk_lock:
                    take = bytes(spk_buf[:need])
                    del spk_buf[:need]        # consume EXACTLY what plays — nothing dropped
                outdata[:] = take.ljust(need, b"\x00")

            mic = sd.RawInputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                    blocksize=2400, device=in_dev, callback=mic_cb)
            spk = sd.RawOutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                     blocksize=2400, device=out_dev, callback=spk_cb)
            with mic, spk:
                on_audio(_beep())  # 🔊 the speaker check: hear the beep = output routing is right
                print("🔊 біп у навушниках? Якщо ні — ./scripts/voice.sh --devices і --out <пристрій>")
                print("listening — говори з нею (Ctrl+C to stop)…")

                async def _mic_watchdog() -> None:
                    await asyncio.sleep(8)
                    if mic_peak["v"] < 500:  # near-silence: likely mic permission / wrong device
                        print("⚠ мікрофон майже тихий (peak "
                              f"{mic_peak['v']}) — перевір дозвіл терміналу на мікрофон "
                              "(System Settings → Privacy → Microphone) і LUMI_STT_DEVICE")

                watchdog = asyncio.create_task(_mic_watchdog())
                try:
                    async for raw in ws:
                        event = json.loads(raw)
                        etype = event.get("type", "")
                        if etype == "error":
                            print(f"❌ API error: {json.dumps(event.get('error', event), ensure_ascii=False)}")
                        elif etype == "session.updated":
                            print("session ready — конфіг прийнято")
                        before = len(dispatcher.unknown)
                        dispatcher.feed(event)
                        if len(dispatcher.unknown) > before:  # discovery, live
                            print(f"  [new event type: {dispatcher.unknown[-1]}]")
                finally:
                    watchdog.cancel()

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

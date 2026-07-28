"""The HYBRID voice probe (the third prototype) — the ElevenLabs hybrid from VOICE_MODE.md:

    mic → **OpenAI Realtime** (`gpt-realtime-2.1`, server VAD + built-in ASR + brain,
    ``output_modalities: ["text"]``) → streamed text → **ElevenLabs** TTS (HER voice) → speaker

A standalone **manual, paid** dev script (never CI), the A/B/C sibling of ``realtime_probe.py``
(all-OpenAI: great latency, awful accent) and ``chain_probe.py`` (Deepgram+Gemini+ElevenLabs: her
voice, extra STT hop). The hybrid keeps what each probe won: the realtime side does VAD + ASR +
generation in ONE server loop (no separate STT round-trip, conversation state held server-side),
and the audio identity is her existing ElevenLabs voice. The open question this probe answers:
what does the extra TTS hop cost on top of realtime's ~1.3 s?

Run:
    ./scripts/voice_hybrid.sh
    ./scripts/voice_hybrid.sh --model gpt-realtime-2.1-mini --tts-model eleven_flash_v2_5

Needs OPENAI_API_KEY + ELEVENLABS_API_KEY + LUMI_VOICE_ID in .env. Headphones. Latency per turn:
``speech_stopped → first ElevenLabs chunk``, split into ``llm first`` (server VAD+ASR+generation to
the first text delta) and ``tts first``. Ctrl+C prints the summary.

The pure pieces (session payload, dispatcher, stats) are unit-tested; ``run()`` is glue. Reuses the
siblings' tested helpers: ``b64_pcm``/``pcm_from_b64`` (realtime), ``VOICE_STYLE`` /
``SentenceAssembler`` / ``clean_sentence`` / ``ELEVEN_STREAM_URL`` (chain) — plus the core v1.4
``StreamTagFilter`` so a leaked <think>/<emotion> never reaches her voice.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root on the path

from scripts.chain_probe import ELEVEN_STREAM_URL, VOICE_STYLE  # noqa: E402
from scripts.realtime_probe import REALTIME_URL, SAMPLE_RATE, b64_pcm  # noqa: E402

DEFAULT_MODEL = "gpt-realtime-2.1"  # the flagship — the accent lost there, but TEXT is its output now


def parse_cli(argv: list[str]) -> dict:
    """Pure CLI parse: the realtime brain + the ElevenLabs model + audio device helpers."""
    import argparse

    ap = argparse.ArgumentParser(
        description="Hybrid voice probe — OpenAI Realtime brain + ElevenLabs voice (A/B/C).",
    )
    ap.add_argument("--model", default=None,
                    help=f"the realtime brain (default: {DEFAULT_MODEL})")
    ap.add_argument("--tts-model", default=None,
                    help="the ElevenLabs model (default: LUMI_VOICE_MODEL; try eleven_flash_v2_5)")
    ap.add_argument("--devices", action="store_true", help="list audio devices and exit")
    ap.add_argument("--out", default=None,
                    help="output device (index or name substring; default: system output)")
    ns = ap.parse_args(argv)
    return {"model": ns.model, "tts_model": ns.tts_model, "devices": ns.devices, "out": ns.out}


def build_hybrid_session(instructions: str, *, lang: str = "uk") -> dict:
    """The ``session.update`` payload: audio IN (pcm16@24k + Ukrainian ASR + semantic VAD), but
    **text OUT** — no voice, no output audio; ElevenLabs owns the sound. The delivery block is the
    chain probe's VOICE_STYLE (short spoken replies, no service tags) — accent steering is gone
    because the built-in voices are out of the loop entirely."""
    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "instructions": VOICE_STYLE + instructions,
            "output_modalities": ["text"],
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
            },
        },
    }


class HybridDispatcher:
    """Route Realtime **text-output** events → callbacks + latency stamps. Pure — fed dicts, timed
    by an injected ``now()``; the sibling of the realtime probe's ProbeDispatcher, with the audio
    legs replaced by text deltas (``response.output_text.delta`` / the older ``response.text.delta``).

    ``llm first`` = VAD ``speech_stopped`` → the first text delta of the following response — the
    server-side VAD+ASR+generation cost in one number (the chain's ``stt + llm`` folded together).
    """

    _TEXT_DELTA = ("response.output_text.delta", "response.text.delta")
    _TEXT_DONE = ("response.output_text.done", "response.text.done")
    _USER_TRANSCRIPT = ("conversation.item.input_audio_transcription.completed",)
    _KNOWN_QUIET = (
        "session.created", "session.updated", "input_audio_buffer.speech_started",
        "input_audio_buffer.speech_stopped", "input_audio_buffer.committed",
        "conversation.item.created", "conversation.item.added", "conversation.item.done",
        "conversation.item.input_audio_transcription.delta",
        "response.created", "response.done", "response.output_item.added",
        "response.output_item.done", "response.content_part.added", "response.content_part.done",
        "rate_limits.updated", "error",
    )

    def __init__(
        self,
        *,
        now: Callable[[], float],
        on_text_delta: Callable[[str], None],
        on_text_done: Callable[[str], None],
        on_user_transcript: Callable[[str], None],
        on_barge_in: Callable[[], None],
        on_first_text: Callable[[float], None] | None = None,
    ) -> None:
        self._now = now
        self._on_delta = on_text_delta
        self._on_done = on_text_done
        self._on_user = on_user_transcript
        self._on_barge_in = on_barge_in
        self._on_first_text = on_first_text
        self._speech_stopped_at: float | None = None
        self._first_text_pending = False
        self._parts: list[str] = []
        self.speech_stopped_at: float | None = None  # the running turn's t0 for the total clock
        self.latencies: list[float] = []             # speech_stop → first text delta
        self.unknown: list[str] = []
        self.errors: list[dict] = []

    def feed(self, event: dict) -> None:
        etype = event.get("type", "")
        if etype == "input_audio_buffer.speech_stopped":
            self._speech_stopped_at = self._now()
            self.speech_stopped_at = self._speech_stopped_at
            self._first_text_pending = True
        elif etype == "input_audio_buffer.speech_started":
            self._on_barge_in()  # he spoke — the glue clears any queued TTS audio
        elif etype in self._TEXT_DELTA:
            if self._first_text_pending and self._speech_stopped_at is not None:
                latency = self._now() - self._speech_stopped_at
                self.latencies.append(latency)
                self._first_text_pending = False
                if self._on_first_text is not None:
                    self._on_first_text(latency)
            delta = event.get("delta", "")
            self._parts.append(delta)
            self._on_delta(delta)
        elif etype in self._TEXT_DONE:
            text = event.get("text") or "".join(self._parts)
            self._parts = []
            self._on_done(text)
        elif etype in self._USER_TRANSCRIPT:
            self._on_user(event.get("transcript", ""))
        elif etype == "error":
            self.errors.append(event.get("error", event))
        elif etype not in self._KNOWN_QUIET:
            if etype not in self.unknown:
                self.unknown.append(etype)


class HybridStats:
    """Per-turn latencies + the session summary — total (speech-stop → first ElevenLabs chunk)
    split into ``llm_first`` (server VAD+ASR+generation) and ``tts_first``."""

    def __init__(self) -> None:
        self.total: list[float] = []
        self.llm: list[float] = []
        self.tts: list[float] = []

    def add(self, *, llm: float, tts: float, total: float) -> None:
        self.llm.append(llm)
        self.tts.append(tts)
        self.total.append(total)

    def summary(self) -> dict:
        if not self.total:
            return {"turns": 0}
        med = statistics.median
        return {
            "turns": len(self.total),
            "median_s": round(med(self.total), 2),
            "min_s": round(min(self.total), 2),
            "max_s": round(max(self.total), 2),
            "stages_median_s": {
                "llm_first": round(med(self.llm), 2),
                "tts_first": round(med(self.tts), 2),
            },
        }


def run() -> None:  # pragma: no cover — live WS + REST + audio hardware glue (manual + paid, never CI)
    import asyncio
    import os
    import threading
    import time
    import urllib.request

    import sounddevice as sd
    import websockets

    from core.agent import build_core
    from core.config import load_config
    from core.streaming import StreamTagFilter
    from scripts.chain_probe import SentenceAssembler, clean_sentence, speakable
    from voice.dictator import resolve_input_device
    from voice.tts import voice_settings_for

    cli = parse_cli(sys.argv[1:])
    if cli["devices"]:
        for i, d in enumerate(sd.query_devices()):
            io = f"in:{d['max_input_channels']} out:{d['max_output_channels']}"
            print(f"{i:3d}  {d['name']}  ({io})")
        return

    # The talking register (as the chain probe): no think directive in the snapshot — the realtime
    # brain answers with prose immediately; the StreamTagFilter stays as the guarantee.
    os.environ["LUMI_REASONING"] = "off"
    cfg = load_config()
    missing = [name for name, val in (("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", "").strip()),
                                      ("ELEVENLABS_API_KEY", cfg.elevenlabs_api_key),
                                      ("LUMI_VOICE_ID", cfg.voice_id)) if not val]
    if missing:
        raise SystemExit(f"the hybrid probe needs {', '.join(missing)} in .env — it is a paid live call.")
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = (cli["model"] or os.getenv("LUMI_REALTIME_MODEL") or DEFAULT_MODEL).strip()
    tts_model = (cli["tts_model"] or cfg.voice_model).strip()

    print("assembling Лілі's prompt snapshot (close the TUI if it's running)…")
    core = build_core(config=cfg)
    session = core.start_session()
    instructions = core.prompt_snapshot(session)
    print(f"snapshot: {len(instructions)} chars · brain={model} (text out) · "
          f"tts={tts_model} (HER voice) · headphones ON?")

    spk_lock = threading.Lock()
    spk_buf = bytearray()

    def spk_cb(outdata, frames, t, status) -> None:
        need = len(outdata)
        with spk_lock:
            take = bytes(spk_buf[:need])
            del spk_buf[:need]
        outdata[:] = take.ljust(need, b"\x00")

    def drain_speaker() -> None:
        with spk_lock:
            had = len(spk_buf) > 0
            spk_buf.clear()
        if had:
            print("  [barge-in]")

    def tts_stream(sentence: str) -> tuple[float, float] | None:
        """One sentence → ElevenLabs stream (pcm 24 k) → the speaker buffer; returns
        ``(seconds_to_first_chunk, monotonic_at_first_chunk)`` — the chain probe's shape."""
        stability, style = voice_settings_for(None)
        body = json.dumps({
            "text": sentence, "model_id": tts_model,
            "voice_settings": {"stability": stability, "similarity_boost": 0.75, "style": style},
        }).encode()
        req = urllib.request.Request(
            ELEVEN_STREAM_URL.format(voice_id=cfg.voice_id) + "?output_format=pcm_24000",
            data=body, method="POST",
            headers={"xi-api-key": cfg.elevenlabs_api_key, "Content-Type": "application/json"},
        )
        t0 = time.monotonic()
        first: tuple[float, float] | None = None
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 — fixed ElevenLabs host
                while chunk := resp.read(4800):
                    if first is None:
                        now = time.monotonic()
                        first = (now - t0, now)
                    with spk_lock:
                        spk_buf.extend(chunk)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠ TTS failed: {exc}")
        return first

    stats = HybridStats()

    async def main() -> None:
        url = f"{REALTIME_URL}?model={model}"
        headers = {"Authorization": f"Bearer {api_key}"}
        async with websockets.connect(url, additional_headers=headers, max_size=1 << 24) as ws:
            await ws.send(json.dumps(build_hybrid_session(instructions)))
            loop = asyncio.get_running_loop()

            # Per-response speech state: the tag filter + sentence assembly + the turn's stamps.
            turn = {"filt": StreamTagFilter(), "asm": SentenceAssembler(),
                    "llm_first": None, "tts_first": None, "total": None}
            tts_q: asyncio.Queue = asyncio.Queue()

            async def tts_worker() -> None:
                """Serialize sentence synthesis off the WS loop — reading never blocks on REST."""
                while True:
                    sentence = await tts_q.get()
                    took = await loop.run_in_executor(None, tts_stream, sentence)
                    if took is not None and turn["tts_first"] is None:
                        turn["tts_first"], first_abs = took
                        if dispatcher.speech_stopped_at is not None:
                            turn["total"] = first_abs - dispatcher.speech_stopped_at
                            print(f"  [first audio {turn['total']:.2f}s = "
                                  f"llm first {turn['llm_first'] or 0:.2f} + "
                                  f"tts first {turn['tts_first']:.2f}]")
                    tts_q.task_done()

            def enqueue(sentence: str) -> None:
                cleaned = clean_sentence(sentence)
                if not cleaned:
                    return
                if not speakable(cleaned):  # an English/markup leak never reaches her voice
                    print(f"  [не озвучено: {cleaned[:80]}]")
                    return
                tts_q.put_nowait(cleaned)

            def on_text_delta(delta: str) -> None:
                shown = turn["filt"].feed(delta)
                if not shown:
                    return
                for sentence in turn["asm"].feed(shown):
                    enqueue(sentence)

            def on_text_done(text: str) -> None:
                tail = turn["filt"].flush()
                if tail:
                    turn["asm"].feed(tail)
                for sentence in turn["asm"].flush():
                    enqueue(sentence)
                # print the FILTERED reply (the raw `text` may carry think/tags)
                filt2 = StreamTagFilter()
                cleaned_full = clean_sentence(filt2.feed(text) + filt2.flush())
                print(f"Лілі: {cleaned_full or clean_sentence(text)}")
                if turn["llm_first"] is not None and turn["total"] is not None:
                    stats.add(llm=turn["llm_first"], tts=turn["tts_first"] or 0.0,
                              total=turn["total"])
                turn["filt"], turn["asm"] = StreamTagFilter(), SentenceAssembler()
                turn["llm_first"] = turn["tts_first"] = turn["total"] = None

            dispatcher = HybridDispatcher(
                now=time.monotonic,
                on_text_delta=on_text_delta,
                on_text_done=on_text_done,
                on_user_transcript=lambda t: print(f"\nYou: {t}"),
                on_barge_in=drain_speaker,
                on_first_text=lambda s: turn.__setitem__("llm_first", s),
            )

            in_dev = resolve_input_device(cfg.stt_device, list(sd.query_devices()))
            out_dev = None
            if cli["out"]:
                spec = cli["out"]
                devs = list(sd.query_devices())
                out_dev = (int(spec) if spec.isdigit() else
                           next((i for i, d in enumerate(devs) if spec.lower() in d["name"].lower()
                                 and d["max_output_channels"] > 0), None))

            def mic_cb(indata, frames, t, status) -> None:
                payload = json.dumps({"type": "input_audio_buffer.append",
                                      "audio": b64_pcm(bytes(indata))})
                asyncio.run_coroutine_threadsafe(ws.send(payload), loop)

            mic = sd.RawInputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                    blocksize=2400, device=in_dev, callback=mic_cb)
            spk = sd.RawOutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                     blocksize=2400, device=out_dev, callback=spk_cb)
            with mic, spk:
                with spk_lock:
                    spk_buf.extend(_beep())
                print("🔊 біп у навушниках? Якщо ні — ./scripts/voice_hybrid.sh --devices і --out <пристрій>")
                print("listening — говори з нею (Ctrl+C to stop)…")
                worker = asyncio.create_task(tts_worker())
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
                        if len(dispatcher.unknown) > before:
                            print(f"  [new event type: {dispatcher.unknown[-1]}]")
                finally:
                    worker.cancel()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        print("\n── session summary ──")
        print(json.dumps(stats.summary(), ensure_ascii=False))


def _beep(rate: int = SAMPLE_RATE, seconds: float = 0.25, freq: float = 440.0) -> bytes:
    """The startup speaker check — same as the sibling probes."""
    import math
    import struct

    n = int(rate * seconds)
    frames = (int(12000 * math.sin(2 * math.pi * freq * i / rate)) for i in range(n))
    return struct.pack(f"<{n}h", *frames)


if __name__ == "__main__":  # pragma: no cover
    run()

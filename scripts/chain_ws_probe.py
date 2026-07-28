"""The STREAMING chain probe (the fourth prototype) — the chain with Deepgram over WebSocket:

    mic → **Deepgram WS** (recognizes DURING speech, its endpointing ends the turn)
        → **Gemini** (streamed) → **ElevenLabs** (HER voice) → speaker

The A/B/C/D fourth: identical to ``chain_probe.py`` except the STT leg. The REST chain paid
~1.4 s median for a batch round-trip AFTER the utterance (plus a hidden 0.8 s local-VAD hangover);
here the transcript is already complete the moment Deepgram's endpointing says «he finished» —
the ``stt`` stage collapses to ~0 and the local energy VAD is gone entirely. Expected median
~1.5–1.7 s (flash-lite) vs the measured REST 2.78 s.

Run:
    ./scripts/voice_chain_ws.sh
    ./scripts/voice_chain_ws.sh --model gemini-2.5-flash --endpoint-ms 300

Needs DEEPGRAM_API_KEY + GEMINI_API_KEY + ELEVENLABS_API_KEY + LUMI_VOICE_ID (.env). Headphones.
Per turn: ``[first audio X = llm first A + tts first B · endpoint C]`` where the total clocks from
the **endpointing decision** (the same semantic moment as the other probes' ``speech_stopped``)
and ``endpoint`` is the diagnostic gap from the last loud mic block to that decision — the felt
extra the printed number hides (~0.3–0.5 s here vs ~0.8 s REST). Ctrl+C prints the summary.

Pure pieces (the Deepgram URL, the utterance tracker over Results/speech_final/UtteranceEnd, the
stats) are unit-tested; ``run()`` is glue. Shares the tested chain helpers: VOICE_STYLE,
build_gemini_body, gemini_sse_delta, SentenceAssembler, clean_sentence, speakable — plus the core
v1.4 StreamTagFilter before her voice.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root on the path

from scripts.chain_probe import (  # noqa: E402 — the tested chain pieces, one seam changed
    DEFAULT_BRAIN,
    DEFAULT_STT_MODEL,
    ELEVEN_STREAM_URL,
    GEMINI_STREAM_URL,
    MIC_RATE,
    RETRYABLE_HTTP,
    SPK_RATE,
    SentenceAssembler,
    build_gemini_body,
    clean_sentence,
    gemini_finish_reason,
    gemini_sse_delta,
    speakable,
)

# v1.6.2 LUMI-200: the streaming-STT pieces were promoted into /voice — the probe is the manual
# harness over the SAME tested adapter (one source of truth, no duplicated tracker).
from voice.stream_stt import (  # noqa: E402
    DEFAULT_ENDPOINT_MS,
    UTTERANCE_END_MS,
    UtteranceTracker,
    build_deepgram_url,
)

_ = UTTERANCE_END_MS  # re-exported for the probe's CLI epilog/tests (the adapter owns the value)


def parse_cli(argv: list[str]) -> dict:
    """Pure CLI parse — the chain probe's picks + the endpointing window."""
    import argparse

    ap = argparse.ArgumentParser(
        description="Streaming chain probe — Deepgram WS + Gemini + ElevenLabs (the fourth A/B).",
    )
    ap.add_argument("--model", default=None, help=f"the brain (default: {DEFAULT_BRAIN})")
    ap.add_argument("--stt-model", default=None,
                    help=f"the Deepgram model (default: {DEFAULT_STT_MODEL})")
    ap.add_argument("--tts-model", default=None,
                    help="the ElevenLabs model (default: LUMI_VOICE_MODEL)")
    ap.add_argument("--endpoint-ms", type=int, default=DEFAULT_ENDPOINT_MS,
                    help=f"Deepgram endpointing silence, ms (default {DEFAULT_ENDPOINT_MS})")
    ap.add_argument("--devices", action="store_true", help="list audio devices and exit")
    ap.add_argument("--out", default=None,
                    help="output device (index or name substring; default: system output)")
    ns = ap.parse_args(argv)
    return {"model": ns.model, "stt_model": ns.stt_model, "tts_model": ns.tts_model,
            "endpoint_ms": ns.endpoint_ms, "devices": ns.devices, "out": ns.out}


class WsStats:
    """Per-turn latencies + the summary — total (endpoint decision → first ElevenLabs chunk),
    with the ``endpoint`` diagnostic (last loud mic block → the decision) reported separately."""

    def __init__(self) -> None:
        self.total: list[float] = []
        self.endpoint: list[float] = []
        self.llm: list[float] = []
        self.tts: list[float] = []

    def add(self, *, endpoint: float, llm: float, tts: float, total: float) -> None:
        self.endpoint.append(endpoint)
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
                "endpoint": round(med(self.endpoint), 2),
                "llm_first": round(med(self.llm), 2),
                "tts_first": round(med(self.tts), 2),
            },
        }


def run() -> None:  # pragma: no cover — live WS/SSE/REST + audio hardware (manual + paid, never CI)
    import asyncio
    import os
    import threading
    import time
    import urllib.request

    import sounddevice as sd
    import websockets

    from core.agent import build_core
    from core.config import load_config
    from core.streaming import StreamTagFilter, decode_json_string_value
    from voice.dictator import resolve_input_device
    from voice.tts import voice_settings_for

    cli = parse_cli(sys.argv[1:])
    if cli["devices"]:
        for i, d in enumerate(sd.query_devices()):
            io = f"in:{d['max_input_channels']} out:{d['max_output_channels']}"
            print(f"{i:3d}  {d['name']}  ({io})")
        return

    # The talking register, as the REST chain: no think directive in the snapshot; the tag filter
    # + the speakable gate stay as guarantees against plain-text leaks.
    os.environ["LUMI_REASONING"] = "off"
    os.environ["LUMI_THINKING"] = "off"
    cfg = load_config()
    missing = [name for name, val in (("DEEPGRAM_API_KEY", cfg.deepgram_api_key),
                                      ("GEMINI_API_KEY", cfg.gemini_api_key),
                                      ("ELEVENLABS_API_KEY", cfg.elevenlabs_api_key),
                                      ("LUMI_VOICE_ID", cfg.voice_id)) if not val]
    if missing:
        raise SystemExit(f"the ws-chain probe needs {', '.join(missing)} in .env — it is a paid live call.")

    brain = (cli["model"] or DEFAULT_BRAIN).strip()
    stt_model = (cli["stt_model"] or DEFAULT_STT_MODEL).strip()
    tts_model = (cli["tts_model"] or cfg.voice_model).strip()

    print("assembling Лілі's prompt snapshot (close the TUI if it's running)…")
    core = build_core(config=cfg)
    session = core.start_session()
    instructions = core.prompt_snapshot(session)
    print(f"snapshot: {len(instructions)} chars · brain={brain} · stt={stt_model} (WS, "
          f"endpoint {cli['endpoint_ms']}ms) · tts={tts_model} · headphones ON?")

    spk_lock = threading.Lock()
    spk_buf = bytearray()

    def spk_cb(outdata, frames, t, status) -> None:
        need = len(outdata)
        with spk_lock:
            take = bytes(spk_buf[:need])
            del spk_buf[:need]
        outdata[:] = take.ljust(need, b"\x00")

    def tts_stream(sentence: str) -> tuple[float, float] | None:
        """One sentence → ElevenLabs stream (pcm 24 k) → the speaker buffer; the chain shape:
        ``(seconds_to_first_chunk, monotonic_at_first_chunk)`` or None on failure."""
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

    stats = WsStats()
    history: list[tuple[str, str]] = []

    def run_turn(text: str, t0: float, endpoint_s: float) -> None:
        """The Gemini+TTS half of a turn — the REST chain's run_turn minus the STT leg (the
        transcript is already in hand at the endpoint decision). Runs in an executor thread."""
        print(f"\nYou: {text}")
        body = json.dumps(build_gemini_body(instructions, history, text, model=brain)).encode()
        req = urllib.request.Request(
            GEMINI_STREAM_URL.format(model=brain) + f"?alt=sse&key={cfg.gemini_api_key}",
            data=body, method="POST", headers={"Content-Type": "application/json"},
        )
        filt = StreamTagFilter()
        assembler = SentenceAssembler()
        reply_parts: list[str] = []
        t_llm_first: float | None = None
        t_tts_first: float | None = None
        t_first_audio: float | None = None
        t_llm0 = time.monotonic()

        def speak(sentence: str) -> None:
            nonlocal t_tts_first, t_first_audio
            sentence = clean_sentence(sentence)
            if not sentence:
                return
            if not speakable(sentence):
                print(f"  [не озвучено: {sentence[:80]}]")
                return
            took = tts_stream(sentence)
            if took is not None and t_tts_first is None:
                t_tts_first, first_abs = took
                t_first_audio = first_abs - t0

        resp = None
        for attempt in (1, 2, 3):
            try:
                resp = urllib.request.urlopen(req, timeout=120)  # noqa: S310 — fixed Gemini host
                break
            except urllib.error.HTTPError as exc:
                if exc.code in RETRYABLE_HTTP and attempt < 3:
                    print(f"  ⚠ Gemini {exc.code} — retry {attempt}/2…")
                    time.sleep(0.5 * attempt)
                    continue
                print(f"  ⚠ brain failed: {exc}")
                return
            except Exception as exc:  # noqa: BLE001
                if attempt < 3:
                    print(f"  ⚠ Gemini unreachable ({exc}) — retry {attempt}/2…")
                    time.sleep(0.5 * attempt)
                    continue
                print(f"  ⚠ brain failed: {exc}")
                return
        raw_json = ""  # the structured stream: decode the growing "reply" field incrementally
        seen = 0
        try:
            with resp:
                for raw in resp:
                    line = raw.strip()
                    if not line.startswith(b"data: "):
                        continue
                    chunk = json.loads(line[6:])
                    cut = gemini_finish_reason(chunk)
                    if cut:
                        print(f"  ⚠ generation cut: finishReason={cut}")
                    raw_json += gemini_sse_delta(chunk)
                    decoded = decode_json_string_value(raw_json)
                    if len(decoded) <= seen:
                        continue
                    shown = filt.feed(decoded[seen:])
                    seen = len(decoded)
                    if not shown:
                        continue
                    if t_llm_first is None:
                        t_llm_first = time.monotonic() - t_llm0
                    reply_parts.append(shown)
                    for sentence in assembler.feed(shown):
                        speak(sentence)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠ brain failed mid-stream: {exc}")
            return
        try:  # end-of-stream: the STRICT parse flushes any tail the conservative decoder held back
            final = json.loads(raw_json).get("reply", "")
        except (ValueError, AttributeError):
            final = ""
        if len(final) > seen:
            shown = filt.feed(final[seen:])
            seen = len(final)
            if shown:
                t_llm_first = t_llm_first or (time.monotonic() - t_llm0)
                reply_parts.append(shown)
                for sentence in assembler.feed(shown):
                    speak(sentence)
        if seen == 0 and raw_json.strip():  # a non-JSON degrade — treat the raw text as the reply
            shown = filt.feed(raw_json)
            if shown:
                t_llm_first = t_llm_first or (time.monotonic() - t_llm0)
                reply_parts.append(shown)
                for sentence in assembler.feed(shown):
                    speak(sentence)
        tail = filt.flush()
        if tail:
            reply_parts.append(tail)
            assembler.feed(tail)
        for sentence in assembler.flush():
            speak(sentence)
        reply = clean_sentence("".join(reply_parts))
        if not reply:
            print("  ⚠ empty reply")
            return
        print(f"Лілі: {reply}")
        history.append((text, reply))
        if t_first_audio is not None:
            stats.add(endpoint=endpoint_s, llm=t_llm_first or 0.0, tts=t_tts_first or 0.0,
                      total=t_first_audio)
            print(f"  [first audio {t_first_audio:.2f}s = llm first {t_llm_first or 0:.2f} + "
                  f"tts first {t_tts_first or 0:.2f} · endpoint {endpoint_s:.2f}]")

    async def main() -> None:
        url = build_deepgram_url(model=stt_model, lang=cfg.stt_lang,
                                 endpoint_ms=cli["endpoint_ms"])
        headers = {"Authorization": f"Token {cfg.deepgram_api_key}"}
        async with websockets.connect(url, additional_headers=headers, max_size=1 << 24) as ws:
            loop = asyncio.get_running_loop()
            audio_q: asyncio.Queue = asyncio.Queue()
            utter_q: asyncio.Queue = asyncio.Queue()
            last_voice = {"ts": 0.0}

            def mic_cb(indata, frames, t, status) -> None:
                data = bytes(indata)
                peak = max((abs(int.from_bytes(data[i:i + 2], "little", signed=True))
                            for i in range(0, min(len(data), 400), 2)), default=0)
                if peak >= 800:  # diagnostic only: when did he last actually sound
                    last_voice["ts"] = time.monotonic()
                loop.call_soon_threadsafe(audio_q.put_nowait, data)

            async def sender() -> None:
                while True:
                    await ws.send(await audio_q.get())  # binary pcm frames straight to Deepgram

            async def turn_worker() -> None:
                while True:
                    text, t0, endpoint_s = await utter_q.get()
                    await loop.run_in_executor(None, run_turn, text, t0, endpoint_s)

            in_dev = resolve_input_device(cfg.stt_device, list(sd.query_devices()))
            out_dev = None
            if cli["out"]:
                spec = cli["out"]
                devs = list(sd.query_devices())
                out_dev = (int(spec) if spec.isdigit() else
                           next((i for i, d in enumerate(devs) if spec.lower() in d["name"].lower()
                                 and d["max_output_channels"] > 0), None))

            tracker = UtteranceTracker()
            mic = sd.RawInputStream(samplerate=MIC_RATE, channels=1, dtype="int16",
                                    blocksize=1600, device=in_dev, callback=mic_cb)
            spk = sd.RawOutputStream(samplerate=SPK_RATE, channels=1, dtype="int16",
                                     blocksize=2400, device=out_dev, callback=spk_cb)
            with mic, spk:
                with spk_lock:
                    spk_buf.extend(_beep())
                print("🔊 біп у навушниках? Якщо ні — ./scripts/voice_chain_ws.sh --devices і --out <пристрій>")
                print("listening — говори з нею (Ctrl+C to stop)…")
                tasks = [asyncio.create_task(sender()), asyncio.create_task(turn_worker())]
                try:
                    async for raw in ws:
                        event = json.loads(raw)
                        utterance = tracker.feed(event)
                        if utterance:
                            t0 = time.monotonic()
                            endpoint_s = max(0.0, t0 - last_voice["ts"]) if last_voice["ts"] else 0.0
                            utter_q.put_nowait((utterance, t0, endpoint_s))
                        elif event.get("type") == "Metadata":
                            pass  # session bookkeeping — quiet
                finally:
                    for task in tasks:
                        task.cancel()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        print("\n── session summary ──")
        print(json.dumps(stats.summary(), ensure_ascii=False))


def _beep(rate: int = SPK_RATE, seconds: float = 0.25, freq: float = 440.0) -> bytes:
    """The startup speaker check — same as the sibling probes."""
    import math
    import struct

    n = int(rate * seconds)
    frames = (int(12000 * math.sin(2 * math.pi * freq * i / rate)) for i in range(n))
    return struct.pack(f"<{n}h", *frames)


if __name__ == "__main__":  # pragma: no cover
    run()

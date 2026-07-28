"""The chained voice probe — the A/B twin of ``realtime_probe.py`` on the CURRENT stack:

    mic → local VAD → **Deepgram** STT (REST) → **Gemini** (streamed) → **ElevenLabs** TTS → speaker

A standalone **manual, paid** dev script (never CI) that answers one question: what first-audio
latency does the chained Deepgram + `gemini-2.5-flash-lite` + ElevenLabs stack give against the
OpenAI Realtime probe's median 1.25–1.37 s — with HER actual ElevenLabs voice, the thing the
realtime built-ins lost on. Same shape as the realtime probe: the session context is the LUMI-196
prompt snapshot, headphones, Ctrl+C prints the summary; but the latency splits into stages
(`stt` / `llm first delta` / `tts first chunk`) so we see where the chain spends its time.

Run:
    ./scripts/voice_chain.sh                 (defaults: gemini-2.5-flash-lite · nova-3 · her voice)
    ./scripts/voice_chain.sh --model gemini-2.5-flash --tts-model eleven_flash_v2_5
    ./scripts/voice_chain.sh --whole         (no sentence streaming — synth the full reply at once)

Needs DEEPGRAM_API_KEY + GEMINI_API_KEY + ELEVENLABS_API_KEY + LUMI_VOICE_ID in .env. The reply is
streamed sentence-by-sentence into TTS (the v1.4 voicer pattern) unless --whole. No barge-in — the
chain has no server VAD over her speech; this probe measures latency, not turn-taking.

The helpers below (CLI, energy VAD, sentence assembly, Gemini body/SSE parse, wav framing, stage
stats) are pure and unit-tested; only ``run()`` touches the network and the audio hardware.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root on the path

DEFAULT_BRAIN = "gemini-2.5-flash"  # flash-lite broke the persona live; flash = «ідеально» (2026-07-29)
DEFAULT_STT_MODEL = "nova-3"
MIC_RATE = 16_000   # the dictator's capture rate — plenty for Nova-3, smaller upload
SPK_RATE = 24_000   # ElevenLabs pcm_24000 → the same speaker format as the realtime probe

GEMINI_STREAM_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent"
)

# v1.6.2 LUMI-201: the speaking-side helpers were promoted into /voice — the probe re-exports the
# SAME tested pieces (one source of truth; the sibling probes import them from here).
from voice.stream_tts import (  # noqa: E402
    ELEVEN_STREAM_URL,
    SentenceAssembler,
    clean_sentence,
    speakable,
)

_ = (ELEVEN_STREAM_URL, SentenceAssembler, clean_sentence, speakable)  # re-exported names

# The voice-mode delivery block PREPENDED to the snapshot — the chain twin of the realtime probe's
# SPEECH_STYLE: short spoken replies, pure Ukrainian, and NO text-protocol tags (the probe learned
# the hard way that they leak into speech).
VOICE_STYLE = (
    "[ГОЛОСОВИЙ РЕЖИМ — НАЙВИЩИЙ ПРІОРИТЕТ]\n"
    "Це жива голосова розмова: твою відповідь одразу озвучують. Відповідай коротко (1–3 речення), "
    "розмовно, тільки українською. Відповідай ОДРАЗУ фінальною реплікою: без блоку <think>, без "
    "розділів роздумів ([ретроспектива], [голоси], [арбітр] тощо — у голосі їх пропусти) і НІКОЛИ "
    "не виписуй міркування, план чи чернетку відповіді текстом — ані українською, ані англійською "
    "(жодних блоків на кшталт «thought», «plan», «self-correction»). Жодних службових позначок — "
    "ані тегів на кшталт <emotion>, <intent>, <style>, ані слова ЕМОЦІЯ, ані markdown: тільки "
    "чиста мова, яку можна вимовити вголос.\n\n"
)

HISTORY_TURNS = 12  # rolling (user, model) pairs the brain sees — the realtime session holds state
                    # server-side; the chain must carry its own

# Gemini returns transient 5xx/429 under load (flash-lite especially) — the core client retries;
# the probe must too, or every burst kills a turn.
RETRYABLE_HTTP = {429, 500, 502, 503, 504}


def parse_cli(argv: list[str]) -> dict:
    """Pure CLI parse — model/STT/TTS picks + audio device helpers. CLI wins over env/config."""
    import argparse

    ap = argparse.ArgumentParser(
        description="Chained voice probe — Deepgram + Gemini + ElevenLabs latency A/B (vs realtime).",
    )
    ap.add_argument("--model", default=None,
                    help=f"the brain (default: {DEFAULT_BRAIN})")
    ap.add_argument("--stt-model", default=None,
                    help=f"the Deepgram model (default: {DEFAULT_STT_MODEL})")
    ap.add_argument("--tts-model", default=None,
                    help="the ElevenLabs model (default: LUMI_VOICE_MODEL — her voicer sound; "
                         "try eleven_flash_v2_5 for the low-latency tier)")
    ap.add_argument("--whole", action="store_true",
                    help="synth the WHOLE reply at once (no sentence streaming — the v1.4 off path)")
    ap.add_argument("--devices", action="store_true", help="list audio devices and exit")
    ap.add_argument("--out", default=None,
                    help="output device (index or name substring; default: system output)")
    ns = ap.parse_args(argv)
    return {"model": ns.model, "stt_model": ns.stt_model, "tts_model": ns.tts_model,
            "whole": ns.whole, "devices": ns.devices, "out": ns.out}


class EnergyVAD:
    """A tiny local end-of-turn detector: peak-energy speech tracking + a silence hangover.

    Pure — fed ``(peak, now)`` per mic block, returns ``"start"`` / ``"stop"`` / ``None``. The
    ``"stop"`` fires when ``hangover_s`` of silence follows speech — the analogue of the realtime
    API's ``speech_stopped`` event, so the latency clocks start at the same semantic moment.
    """

    def __init__(self, *, start_peak: int = 1500, keep_peak: int = 800,
                 hangover_s: float = 0.8) -> None:
        self._start_peak = start_peak
        self._keep_peak = keep_peak
        self._hangover_s = hangover_s
        self._in_speech = False
        self._last_voice: float | None = None

    def feed(self, peak: int, now: float) -> str | None:
        if not self._in_speech:
            if peak >= self._start_peak:
                self._in_speech = True
                self._last_voice = now
                return "start"
            return None
        if peak >= self._keep_peak:
            self._last_voice = now
            return None
        if self._last_voice is not None and now - self._last_voice >= self._hangover_s:
            self._in_speech = False
            self._last_voice = None
            return "stop"
        return None


def build_gemini_body(instructions: str, history: list[tuple[str, str]], user_text: str,
                      *, model: str, max_tokens: int = 2048) -> dict:
    """The ``streamGenerateContent`` body: snapshot as systemInstruction, rolling history, thinking
    OFF for the flash family (latency is the point; -lite/-flash accept ``thinkingBudget: 0``,
    reasoning models would 400 — omit the config there, as core/llm.py does). ``max_tokens`` keeps
    headroom for a plain-text <thought> draft the model may write DESPITE the instruction (seen
    live) — at 1024 the draft starved the actual reply to nothing."""
    contents: list[dict] = []
    for user, model_reply in history[-HISTORY_TURNS:]:
        contents.append({"role": "user", "parts": [{"text": user}]})
        contents.append({"role": "model", "parts": [{"text": model_reply}]})
    contents.append({"role": "user", "parts": [{"text": user_text}]})
    # STRUCTURED output — the same mechanism the core uses for the emotion contract: constrained
    # JSON leaves NO room for a plain-text CoT preamble (flash-lite wrote one live in three shapes —
    # a bare "thought" block, <thought> tags, then "(_thought_" — quoting the prohibition while
    # violating it; instructions alone cannot stop it). The reply field streams incrementally via
    # core.streaming.decode_json_string_value.
    generation: dict = {
        "maxOutputTokens": max_tokens,
        "responseMimeType": "application/json",
        "responseSchema": {"type": "OBJECT", "properties": {"reply": {"type": "STRING"}},
                           "required": ["reply"]},
    }
    if "flash" in model:
        generation["thinkingConfig"] = {"thinkingBudget": 0}
    return {
        "systemInstruction": {"parts": [{"text": VOICE_STYLE + instructions}]},
        "contents": contents,
        "generationConfig": generation,
        # The core's permissive thresholds (core/llm.py _GEMINI_SAFETY): without them Gemini's
        # DEFAULT safety/recitation filter cuts generation MID-SENTENCE (finishReason != STOP) —
        # the live WS run lost reply tails exactly this way.
        "safetySettings": [
            {"category": c, "threshold": "BLOCK_NONE"}
            for c in ("HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
                      "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")
        ],
    }


def gemini_sse_delta(chunk: dict) -> str:
    """Pull the visible text out of one parsed SSE chunk (thought parts skipped)."""
    parts = ((chunk.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def gemini_finish_reason(chunk: dict) -> str | None:
    """The candidate's ``finishReason`` when it is a CUT (``SAFETY``/``RECITATION``/``MAX_TOKENS``…)
    — ``None`` for a normal ``STOP`` or a mid-stream chunk. The live WS run lost sentence tails to
    exactly such cuts; surfacing the reason names the culprit."""
    reason = (chunk.get("candidates") or [{}])[0].get("finishReason")
    return reason if reason and reason != "STOP" else None


def wav_wrap(pcm: bytes, samplerate: int = MIC_RATE) -> bytes:
    """Raw 16-bit mono PCM → a WAV container (what the Deepgram adapter expects)."""
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(samplerate)
        w.writeframes(pcm)
    return buf.getvalue()


class StageStats:
    """Per-turn stage latencies + the session summary (the realtime probe's shape, plus stages)."""

    def __init__(self) -> None:
        self.total: list[float] = []
        self.stt: list[float] = []
        self.llm: list[float] = []
        self.tts: list[float] = []

    def add(self, *, stt: float, llm: float, tts: float, total: float) -> None:
        self.stt.append(stt)
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
                "stt": round(med(self.stt), 2),
                "llm_first": round(med(self.llm), 2),
                "tts_first": round(med(self.tts), 2),
            },
        }


def _sse_events(resp):  # pragma: no cover — thin network iterator
    """Yield parsed JSON chunks from a ``alt=sse`` response (lines starting ``data: ``)."""
    for raw in resp:
        line = raw.strip()
        if line.startswith(b"data: "):
            yield json.loads(line[6:])


def run() -> None:  # pragma: no cover — live REST/SSE + audio hardware glue (manual + paid, never CI)
    import queue
    import threading
    import time
    import urllib.request

    import sounddevice as sd

    from core.agent import build_core
    from core.config import load_config
    from core.streaming import StreamTagFilter, decode_json_string_value
    from voice.dictator import resolve_input_device
    from voice.stt import DeepgramSTT
    from voice.tts import voice_settings_for

    cli = parse_cli(sys.argv[1:])
    if cli["devices"]:
        for i, d in enumerate(sd.query_devices()):
            io = f"in:{d['max_input_channels']} out:{d['max_output_channels']}"
            print(f"{i:3d}  {d['name']}  ({io})")
        return

    import os

    # The chain's TALKING register (the LATENCY.md S4 idea): no think phase in voice — with
    # LUMI_REASONING=off the snapshot carries NO reasoning/inner-voice directive at all, so the
    # brain streams prose immediately instead of a 4–6 s <think> block before the first sentence
    # (the second live run measured exactly that). LUMI_THINKING off too — nothing may surface
    # native thought summaries here. The StreamTagFilter + the speakable gate stay as guarantees
    # (a flash-tier model can still write PLAIN-TEXT reasoning no flag governs).
    os.environ["LUMI_REASONING"] = "off"
    os.environ["LUMI_THINKING"] = "off"
    cfg = load_config()  # .env first — all three keys may live there
    missing = [name for name, val in (("DEEPGRAM_API_KEY", cfg.deepgram_api_key),
                                      ("GEMINI_API_KEY", cfg.gemini_api_key),
                                      ("ELEVENLABS_API_KEY", cfg.elevenlabs_api_key),
                                      ("LUMI_VOICE_ID", cfg.voice_id)) if not val]
    if missing:
        raise SystemExit(f"the chain probe needs {', '.join(missing)} in .env — it is a paid live call.")

    brain = (cli["model"] or DEFAULT_BRAIN).strip()
    stt_model = (cli["stt_model"] or DEFAULT_STT_MODEL).strip()
    tts_model = (cli["tts_model"] or cfg.voice_model).strip()
    stt = DeepgramSTT(cfg.deepgram_api_key, model=stt_model)

    print("assembling Лілі's prompt snapshot (close the TUI if it's running)…")
    core = build_core(config=cfg)
    session = core.start_session()
    instructions = core.prompt_snapshot(session)
    mode = "whole-reply" if cli["whole"] else "sentence-streamed"
    print(f"snapshot: {len(instructions)} chars · brain={brain} · stt={stt_model} · "
          f"tts={tts_model} ({mode}) · headphones ON?")

    # --- the lossless speaker buffer, exactly as the realtime probe (truncation garbled the voice).
    spk_lock = threading.Lock()
    spk_buf = bytearray()

    def speaker_playing() -> bool:
        with spk_lock:
            return len(spk_buf) > 0

    def spk_cb(outdata, frames, t, status) -> None:
        need = len(outdata)
        with spk_lock:
            take = bytes(spk_buf[:need])
            del spk_buf[:need]
        outdata[:] = take.ljust(need, b"\x00")

    def tts_stream(sentence: str) -> tuple[float, float] | None:
        """Synth one sentence via the ElevenLabs STREAM endpoint (pcm 24 k) into the speaker buffer;
        return ``(seconds_to_first_chunk, monotonic_at_first_chunk)`` — the absolute stamp lets the
        caller clock first-audio at the CHUNK, not after the whole sentence streamed. ``None`` on
        failure (the turn degrades, not dies)."""
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
        except Exception as exc:  # noqa: BLE001 — a failed sentence degrades to silence, never a crash
            print(f"  ⚠ TTS failed: {exc}")
        return first

    # --- mic: light callback → queue; the main loop drives the VAD + the turn.
    mic_q: queue.Queue = queue.Queue()

    def mic_cb(indata, frames, t, status) -> None:
        data = bytes(indata)
        peak = max((abs(int.from_bytes(data[i:i + 2], "little", signed=True))
                    for i in range(0, len(data), 2)), default=0)
        mic_q.put((data, peak))

    in_dev = resolve_input_device(cfg.stt_device, list(sd.query_devices()))
    out_dev = None
    if cli["out"]:
        spec = cli["out"]
        devs = list(sd.query_devices())
        out_dev = (int(spec) if spec.isdigit() else
                   next((i for i, d in enumerate(devs) if spec.lower() in d["name"].lower()
                         and d["max_output_channels"] > 0), None))

    vad = EnergyVAD()
    stats = StageStats()
    history: list[tuple[str, str]] = []
    preroll: list[bytes] = []   # a few blocks BEFORE "start" so the first word isn't clipped
    recording: list[bytes] = []
    in_turn = False

    mic = sd.RawInputStream(samplerate=MIC_RATE, channels=1, dtype="int16",
                            blocksize=1600, device=in_dev, callback=mic_cb)
    spk = sd.RawOutputStream(samplerate=SPK_RATE, channels=1, dtype="int16",
                             blocksize=2400, device=out_dev, callback=spk_cb)

    def run_turn(audio: bytes) -> None:
        t0 = time.monotonic()
        try:
            text = (stt.recognize(wav_wrap(audio), lang=cfg.stt_lang) or "").strip()
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠ STT failed: {exc}")
            return
        t_stt = time.monotonic() - t0
        if len(text) < 2:  # the dictator's better-silent-than-garbage rule
            return
        print(f"\nYou: {text}")

        body = json.dumps(build_gemini_body(instructions, history, text, model=brain)).encode()
        req = urllib.request.Request(
            GEMINI_STREAM_URL.format(model=brain) + f"?alt=sse&key={cfg.gemini_api_key}",
            data=body, method="POST", headers={"Content-Type": "application/json"},
        )
        # The snapshot's inner-voice instruction makes her stream a <think> block + emotion/intent
        # tags — the v1.4 StreamTagFilter hides the reasoning and the tags so ONLY the spoken prose
        # reaches the sentence assembler / TTS (the first live run SPOKE the whole think block).
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
            if not speakable(sentence):  # an English/none-Ukrainian leak — never reaches her voice
                print(f"  [не озвучено: {sentence[:80]}…]" if len(sentence) > 80
                      else f"  [не озвучено: {sentence}]")
                return
            took = tts_stream(sentence)
            if took is not None and t_tts_first is None:
                t_tts_first, first_abs = took
                t_first_audio = first_abs - t0  # clocked at the first CHUNK, not after the sentence

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
            except Exception as exc:  # noqa: BLE001 — URLError/timeout: same degrade, never a crash
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
                for chunk in _sse_events(resp):
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
                    if t_llm_first is None:  # the first SPEAKABLE prose — think time counts here
                        t_llm_first = time.monotonic() - t_llm0
                    reply_parts.append(shown)
                    if not cli["whole"]:
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
                if not cli["whole"]:
                    for sentence in assembler.feed(shown):
                        speak(sentence)
        if seen == 0 and raw_json.strip():  # a non-JSON degrade — treat the raw text as the reply
            shown = filt.feed(raw_json)
            if shown:
                t_llm_first = t_llm_first or (time.monotonic() - t_llm0)
                reply_parts.append(shown)
                if not cli["whole"]:
                    for sentence in assembler.feed(shown):
                        speak(sentence)
        tail = filt.flush()
        if tail:
            reply_parts.append(tail)
            assembler.feed(tail)
        pending = assembler.flush() if not cli["whole"] else ["".join(reply_parts).strip()]
        for sentence in pending:
            speak(sentence)
        reply = clean_sentence("".join(reply_parts))
        if not reply:
            print("  ⚠ empty reply")
            return
        print(f"Лілі: {reply}")
        history.append((text, reply))
        if t_first_audio is not None:
            stats.add(stt=t_stt, llm=t_llm_first or 0.0, tts=t_tts_first or 0.0,
                      total=t_first_audio)
            print(f"  [first audio {t_first_audio:.2f}s = stt {t_stt:.2f} + "
                  f"llm first {t_llm_first or 0:.2f} + tts first {t_tts_first or 0:.2f}]")

    try:
        with mic, spk:
            with spk_lock:
                spk_buf.extend(_beep())
            print("🔊 біп у навушниках? Якщо ні — ./scripts/voice_chain.sh --devices і --out <пристрій>")
            print("listening — говори з нею (Ctrl+C to stop)…")
            while True:
                data, peak = mic_q.get()
                event = vad.feed(peak, time.monotonic())
                if event == "start":
                    recording = list(preroll)
                    in_turn = True
                if in_turn:
                    recording.append(data)
                else:
                    preroll = (preroll + [data])[-3:]
                if event == "stop":
                    in_turn = False
                    run_turn(b"".join(recording))
                    recording, preroll = [], []
                    while speaker_playing():  # let her finish before listening again (headphones,
                        time.sleep(0.1)       # but a half-played buffer would smear the next VAD)
                    while not mic_q.empty():  # drop the mic backlog piled up during the turn
                        mic_q.get_nowait()
    except KeyboardInterrupt:
        pass
    finally:
        print("\n── session summary ──")
        print(json.dumps(stats.summary(), ensure_ascii=False))


def _beep(rate: int = SPK_RATE, seconds: float = 0.25, freq: float = 440.0) -> bytes:
    """The startup speaker check — same as the realtime probe."""
    import math
    import struct

    n = int(rate * seconds)
    frames = (int(12000 * math.sin(2 * math.pi * freq * i / rate)) for i in range(n))
    return struct.pack(f"<{n}h", *frames)


if __name__ == "__main__":  # pragma: no cover
    run()

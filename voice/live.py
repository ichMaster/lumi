"""The in-TUI live voice loop (v1.6.2 LUMI-202) — mic → Deepgram WS → one Core turn → ElevenLabs.

No separate application: the TUI is the only brain-holder, so the loop lives in its asyncio event
loop (`/mode-set voice` starts it, `/mode-set text` stops it). This module owns the LOOP LOGIC —
event routing, the barge-in decision, the synth pump — with every side injected:

* ``stream`` — a :class:`voice.stream_stt.DeepgramStream` (a fake socket in tests);
* ``tracker`` — the phrase-hold :class:`voice.stream_stt.UtteranceTracker`;
* ``pipeline`` — the :class:`voice.stream_tts.SpeechPipeline` (speaker buffer + guards + barge-in);
* ``on_utterance`` — a **non-blocking** callback (the TUI schedules the turn as a worker — the
  event pump must keep reading while she thinks/speaks, or barge-in would go deaf);
* ``on_note`` — one-line status surfacing (``[barge-in]``, drops) for the TUI log.

**Barge-in rule (the v1.6.2 DoD):** a non-empty interim ``Results`` while she is audible (buffer
playing or sentences queued) → :meth:`SpeechPipeline.interrupt` — playback only, never the
committed turn. The mic capture and the speaker device are `sounddevice` glue owned by the TUI
layer (:func:`open_audio` below) — hardware-touching, uncovered by design.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from voice.stream_stt import DeepgramStream, UtteranceTracker
from voice.stream_tts import SpeechPipeline

SPK_RATE = 24_000
MIC_RATE = 16_000
MIC_BLOCK = 1600   # 0.1 s @ 16 kHz — the dictator's proven block size
SPK_BLOCK = 2400   # 0.1 s @ 24 kHz


class VoiceLoop:
    """Route Deepgram events → barge-in + committed utterances; pump queued sentences into TTS."""

    def __init__(
        self,
        *,
        stream: DeepgramStream,
        pipeline: SpeechPipeline,
        on_utterance: Callable[[str], None],
        tracker: UtteranceTracker | None = None,
        on_note: Callable[[str], None] | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._stream = stream
        self._pipeline = pipeline
        self._tracker = tracker if tracker is not None else UtteranceTracker()
        self._on_utterance = on_utterance
        self._on_note = on_note or (lambda _line: None)
        self._now = now or time.monotonic
        self.utterances: list[str] = []  # every committed utterance, for tests/diagnosis
        # v1.6.3 LUMI-206 — the stage line's first stamp. The audio glue updates last_voice_ts on
        # every loud mic block (the probes' diagnostic); a commit records its time + the endpoint
        # gap (last loud sound → the commit decision — the felt part the other stages don't see).
        self.last_voice_ts = 0.0
        self.last_commit: dict | None = None  # {"ts": monotonic, "endpoint_s": float | None}

    # --- the decision core (pure — fed parsed events) ----------------------------------------------
    def handle_event(self, event: dict) -> str | None:
        """One Deepgram event → maybe a barge-in, maybe a committed utterance (returned + routed).

        A barge-in only PAUSES her (voice/stream_tts.SpeechPipeline.interrupt — nothing queued is
        discarded); once your utterance commits — whatever triggered it, a real pause-then-continue
        included — :meth:`resume` lets her finish what she hadn't said, then continue into whatever
        comes next, all still in FIFO order. This also covers stacked commits (you paused mid-
        thought, a request already fired, then you continued): every commit calls resume(), so
        nothing queued ever gets silently skipped."""
        if self._is_barge_in(event):
            self._pipeline.interrupt()
            self._on_note("[barge-in]")
        utterance = self._tracker.feed(event)
        if utterance:
            now = self._now()  # LUMI-206: the commit stamp + the endpoint diagnostic
            self.last_commit = {
                "ts": now,
                "endpoint_s": max(0.0, now - self.last_voice_ts) if self.last_voice_ts else None,
            }
            self._pipeline.resume()
            self.utterances.append(utterance)
            self._on_utterance(utterance)  # non-blocking — the TUI schedules the turn
        elif self._speech_settled(event):
            # He stopped speaking but NOTHING committed — a noise/trailing interim triggered the
            # barge-in and its final came back empty (or the UtteranceEnd found nothing pending).
            # Without this resume the pause had no lifter at all: the live "sound gone forever"
            # (a committed utterance was the ONLY resume path). A held partial (un-punctuated
            # speech_final) deliberately does NOT settle — he's mid-thought; its UtteranceEnd
            # backstop commits it within ~1 s and resumes through the branch above.
            self._pipeline.resume()
        return utterance

    @staticmethod
    def _speech_settled(event: dict) -> bool:
        """True when the event says «he is done speaking» with nothing left pending: an EMPTY final
        transcript (the interrupting sound was noise) or an ``UtteranceEnd`` (the backstop — if it
        had anything to flush, the tracker returned it and the commit branch already resumed)."""
        etype = event.get("type", "")
        if etype == "UtteranceEnd":
            return True
        if etype == "Results" and event.get("is_final"):
            alt = (((event.get("channel") or {}).get("alternatives")) or [{}])[0]
            return not (alt.get("transcript") or "").strip()
        return False

    def _is_barge_in(self, event: dict) -> bool:
        """He is speaking (a non-empty interim) while she is audible, OR about to be (queued and not
        already paused) → stop her sound. Once paused, this is False until :meth:`resume` — the
        pause state itself is the debounce, so a burst of interim events fires exactly one barge-in."""
        if event.get("type") != "Results" or event.get("is_final"):
            return False
        alt = (((event.get("channel") or {}).get("alternatives")) or [{}])[0]
        if not (alt.get("transcript") or "").strip():
            return False
        return self._pipeline.buffer.playing or (
            not self._pipeline.paused and self._pipeline.pending > 0
        )

    # --- the async pumps (thin — drive the injected seams) -----------------------------------------
    async def pump_events(self) -> None:
        """Read the Deepgram socket until it closes; every event goes through :meth:`handle_event`."""
        async for event in self._stream.events():
            self.handle_event(event)

    async def pump_events_resilient(self, *, reconnect: int = 3, backoff_s: float = 1.0) -> None:
        """v1.6.3 LUMI-205 — :meth:`pump_events` that HEALS: a socket drop (error or a server-side
        close) triggers up to ``reconnect`` in-place reopen attempts with exponential backoff
        (``backoff_s`` · 1/2/4…); each successful reconnect gets a **fresh tracker** (a
        half-utterance across a drop is unrecoverable and must not corrupt the next one) and resets
        the retry budget. Returns normally only when a whole outage's retries exhaust — the caller
        (the TUI) then degrades to text. The mic keeps capturing throughout: frames into the dead
        socket are dropped by the sender guard, never an exception storm."""
        import asyncio

        while True:
            try:
                await self.pump_events()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — a dropped socket is the EXPECTED failure here
                pass
            healed = False
            for attempt in range(1, max(0, reconnect) + 1):
                self._on_note(f"voice: reconnecting {attempt}/{reconnect}…")
                await asyncio.sleep(backoff_s * (2 ** (attempt - 1)))
                try:
                    await self._stream.reopen()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 — this attempt failed; the next backs off longer
                    continue
                self._tracker = UtteranceTracker()  # never resume into a half-parsed utterance
                self._on_note("voice: reconnected")
                healed = True
                break
            if not healed:
                return  # retries exhausted — the caller shows the readable degrade to text

    async def synth_pump(self, *, poll_s: float = 0.05) -> None:
        """Drain queued sentences into the speaker buffer off the event loop (blocking TTS reads
        run in a thread). Cancelled by the TUI when voice mode stops."""
        import asyncio

        while True:
            spoke = await asyncio.to_thread(self._pipeline.synth_next)
            if spoke is None:
                await asyncio.sleep(poll_s)


def open_audio(loop, stream: DeepgramStream, pipeline: SpeechPipeline,
               *, mic_device=None, out_device=None,
               voice_loop: VoiceLoop | None = None):  # pragma: no cover — sounddevice glue
    """Open the mic → WS sender and the speaker ← buffer player; returns ``(mic, spk)`` streams.

    Hardware + network glue (headphones — no AEC in the local phases): the mic callback ships raw
    pcm frames onto the asyncio loop; the speaker callback pulls exactly what plays (the lossless
    rule). With a ``voice_loop``, loud mic blocks stamp its ``last_voice_ts`` (the LUMI-206
    endpoint diagnostic — the probes' cheap peak check over the block head). The caller owns
    start/stop/close."""
    import asyncio

    import sounddevice as sd

    def mic_cb(indata, frames, t, status) -> None:
        data = bytes(indata)
        if voice_loop is not None:
            peak = max((abs(int.from_bytes(data[i:i + 2], "little", signed=True))
                        for i in range(0, min(len(data), 200), 2)), default=0)
            if peak >= 800:
                voice_loop.last_voice_ts = time.monotonic()
        asyncio.run_coroutine_threadsafe(stream.send_pcm(data), loop)

    def spk_cb(outdata, frames, t, status) -> None:
        need = len(outdata)
        outdata[:] = pipeline.buffer.pull(need).ljust(need, b"\x00")

    mic = sd.RawInputStream(samplerate=MIC_RATE, channels=1, dtype="int16",
                            blocksize=MIC_BLOCK, device=mic_device, callback=mic_cb)
    spk = sd.RawOutputStream(samplerate=SPK_RATE, channels=1, dtype="int16",
                             blocksize=SPK_BLOCK, device=out_device, callback=spk_cb)
    return mic, spk

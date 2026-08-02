"""Streaming TTS output (v1.6.2 LUMI-201) — sentence-streamed ElevenLabs + the speaker pipeline.

The speaking half of voice mode, promoted from the probes with their live-tuned guards:

* :class:`SentenceAssembler` / :func:`clean_sentence` / :func:`speakable` — the v1.4 sentence rule
  in incremental form, the plain ``ЕМОЦІЯ:`` trailer strip, and the Latin-vs-Cyrillic gate (an
  untagged English CoT and a stray obscenity were both SPOKEN live before it existed).
* :class:`ElevenLabsStreamTTS` — the ElevenLabs **stream** endpoint (``pcm_24000`` — straight into
  a 24 kHz speaker, no mp3 decode), stdlib urllib, injectable ``_opener`` for tests. The batch twin
  is :class:`voice.tts.ElevenLabsTTS` (the v0.14 voicer); this one yields chunks as they generate.
* :class:`SpeakerBuffer` — the lossless lock-protected byte buffer (the probes' proven shape: a
  truncating callback garbled the voice; consume exactly what plays).
* :class:`SpeechPipeline` — the composition the live loop drives: reply deltas in (a defensive
  :class:`core.streaming.StreamTagFilter` — the core filters too, this is the belt), whole clean
  speakable sentences queued, ``synth_next()`` streams one sentence into the buffer, and
  **barge-in** via :meth:`interrupt` — clear the buffer, drop the queue, abandon the in-flight
  sentence's remaining chunks (the epoch check). **Playback only — never the committed turn**: the
  pipeline has no notion of a turn to cancel; the `Message` keeps the full reply text.

Everything here is pure/injected — no audio hardware, no network, no paid CI.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from collections.abc import Iterator

from core.streaming import StreamTagFilter
from voice.tts import voice_settings_for

ELEVEN_STREAM_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
SPK_RATE = 24_000  # ElevenLabs pcm_24000 → the speaker stream's sample rate


# The v1.4 sentence rule (voice/sentences.py): break only at whitespace FOLLOWING . ! ? … — a word
# is never cut. The assembler is its incremental form for a streamed reply.
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")


class SentenceAssembler:
    """Accumulate streamed text deltas → emit whole sentences as soon as they complete."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, delta: str) -> list[str]:
        self._buf += delta
        parts = _SENT_SPLIT_RE.split(self._buf)
        if len(parts) == 1:
            return []
        self._buf = parts[-1]  # the (possibly incomplete) tail stays buffered
        return [p.strip() for p in parts[:-1] if p.strip()]

    def flush(self) -> list[str]:
        tail, self._buf = self._buf.strip(), ""
        return [tail] if tail else []


# v1.6.3 LUMI-203: a clause boundary the first chunk may cut at — , ; : followed by whitespace.
# The em-dash is deliberately NOT a boundary (a chunk ending in «—» sounds broken); word-hyphens
# («світло-сірий») are untouched by construction (no whitespace after the hyphen).
_CLAUSE_BOUNDARY_RE = re.compile(r"[,;:]\s+")
_MIN_CLAUSE_WORDS = 3  # a 1–2-word opening fragment reads badly — hold until the clause clears it


class FirstClauseAssembler(SentenceAssembler):
    """LUMI-203 — the first-clause cut: the probes measured ~0.3–0.65 s of the felt first-audio
    latency sitting in the wait for the FULL first sentence. Until the first chunk of a turn has
    been emitted, a clause boundary (with ≥ ``_MIN_CLAUSE_WORDS`` words before it) or a complete-
    word threshold (``first_words``) also flushes; after that first emission the turn reverts to
    whole-sentence splitting (prosody suffers least at the opening word groups). Never cuts
    mid-word — the threshold path emits only words already followed by whitespace."""

    def __init__(self, first_words: int = 8) -> None:
        super().__init__()
        self._first_words = max(1, first_words)
        self._armed = True  # until ANYTHING is emitted this turn

    def feed(self, delta: str) -> list[str]:
        out = super().feed(delta)  # a completed sentence always wins (and disarms)
        if out:
            self._armed = False
            return out
        if not self._armed:
            return []
        # No full sentence yet — the FIRST clause boundary with enough words before it cuts.
        for m in _CLAUSE_BOUNDARY_RE.finditer(self._buf):
            left = self._buf[: m.start() + 1]  # keep the punctuation on the spoken chunk
            if len(left.split()) >= _MIN_CLAUSE_WORDS:
                self._buf = self._buf[m.end():]
                self._armed = False
                return [left.strip()]
        # …else the word threshold: emit the first N COMPLETE words (never a partial tail).
        complete = self._buf.split() if self._buf[-1:].isspace() else self._buf.split()[:-1]
        if len(complete) >= self._first_words:
            head = complete[: self._first_words]
            idx = 0
            for word in head:  # walk the real buffer so inner whitespace survives the rebuild
                idx = self._buf.index(word, idx) + len(word)
            self._buf = self._buf[idx:].lstrip()
            self._armed = False
            return [" ".join(head)]
        return []


# The last line of defense before TTS: a plain "ЕМОЦІЯ: …" trailer (the thought-format shape the
# StreamTagFilter's tag grammar doesn't cover) must never be spoken.
_EMOTION_LINE_RE = re.compile(r"^\s*ЕМОЦІЯ:.*$", re.MULTILINE)


def clean_sentence(text: str) -> str:
    """Strip an unspoken trailer (a plain ЕМОЦІЯ: line) + collapse leftover whitespace."""
    return re.sub(r"[ \t]{2,}", " ", _EMOTION_LINE_RE.sub("", text)).strip()


_LATIN_RE = re.compile(r"[A-Za-z]")
_CYRILLIC_RE = re.compile(r"[А-Яа-яЄєІіЇїҐґ]")


def speakable(sentence: str) -> bool:
    """The TTS gate: HER spoken lines are Ukrainian — a sentence dominated by Latin letters is a
    LEAK (an untagged English reasoning block, a stray obscenity, raw code), not speech. Both were
    produced live; no flag governs plain-text CoT, so the gate does. Mixed lines with a real
    Ukrainian part (a product name mid-sentence) stay speakable."""
    latin = len(_LATIN_RE.findall(sentence))
    cyrillic = len(_CYRILLIC_RE.findall(sentence))
    if latin + cyrillic == 0:
        return False  # nothing pronounceable (bare punctuation/markup)
    return cyrillic >= latin


class ElevenLabsStreamTTS:
    """One sentence → the ElevenLabs STREAM endpoint → pcm chunks as they generate.

    ``_opener`` is an injectable ``(url, headers, body) -> iterable[bytes]`` so tests capture the
    request and script the chunks; the real path reads the chunked HTTP response via stdlib urllib
    (no SDK — the same discipline as the Deepgram adapter). ``emotion`` biases delivery through the
    shared :func:`voice.tts.voice_settings_for` (presentation only, never the text)."""

    def __init__(self, api_key: str, voice_id: str, model: str = "eleven_multilingual_v2",
                 *, _opener=None) -> None:
        self.voice_id = voice_id
        self.model = model
        self._api_key = api_key  # secret — never logged
        self._opener = _opener

    def stream(self, text: str, *, emotion: str | None = None) -> Iterator[bytes]:
        stability, style = voice_settings_for(emotion)
        url = ELEVEN_STREAM_URL.format(voice_id=self.voice_id) + "?output_format=pcm_24000"
        headers = {"xi-api-key": self._api_key, "Content-Type": "application/json"}
        body = json.dumps({
            "text": text, "model_id": self.model,
            "voice_settings": {"stability": stability, "similarity_boost": 0.75, "style": style},
        }).encode()
        if self._opener is not None:
            yield from self._opener(url, headers, body)
            return
        import urllib.request  # pragma: no cover — the real paid path (never CI)

        req = urllib.request.Request(url, data=body, method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 — fixed ElevenLabs host
            while chunk := resp.read(4800):
                yield chunk


class MockStreamTTS:
    """A canned streaming TTS for tests — records sentences (+ per-call emotion), yields chunks."""

    def __init__(self, chunks: list[bytes] | None = None) -> None:
        self.calls: list[str] = []
        self.emotions: list[str | None] = []  # LUMI-204: the delivery bias each sentence carried
        self._chunks = chunks if chunks is not None else [b"AUDIO"]

    def stream(self, text: str, *, emotion: str | None = None) -> Iterator[bytes]:
        self.calls.append(text)
        self.emotions.append(emotion)
        yield from self._chunks


class SpeakerBuffer:
    """The lossless speaker buffer: feed whole chunks, pull exactly what plays, clear on barge-in.

    (The queue-and-truncate version DROPPED chunk remainders → a garbled voice, live.)"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> None:
        with self._lock:
            self._buf.extend(chunk)

    def pull(self, need: int) -> bytes:
        """Up to ``need`` bytes, consumed exactly (the audio-callback glue pads with silence)."""
        with self._lock:
            take = bytes(self._buf[:need])
            del self._buf[:need]
        return take

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    @property
    def playing(self) -> bool:
        with self._lock:
            return len(self._buf) > 0


class SpeechPipeline:
    """Reply deltas in → her voice out, with barge-in **as a queue, not a discard**. The live loop
    drives it:

    ``feed_delta`` per streamed chunk (+ ``finish_turn`` at the end) queues whole clean speakable
    sentences; ``synth_next()`` (called from a worker/executor — it blocks on the TTS read) streams
    ONE queued sentence into the speaker buffer and returns its text, or ``None`` when idle/paused.
    :meth:`interrupt` is the barge-in: stops the SOUND immediately (clears the buffer, aborts the
    in-flight sentence) and **pauses** — but the queued backlog (including sentences a still-
    streaming turn keeps adding) is NEVER discarded. :meth:`resume` (called once your utterance
    commits) lifts the pause: she finishes what she hadn't said yet, in order, then continues into
    whatever came after — nothing she was going to say is silently skipped."""

    def __init__(self, tts, buffer: SpeakerBuffer | None = None,
                 *, auto_resume_s: float = 4.0, first_clause_words: int = 0,
                 emotion_supplier=None) -> None:
        self._tts = tts
        self.buffer = buffer if buffer is not None else SpeakerBuffer()
        # v1.6.3 LUMI-204: her voice carries her state — a callable returning the CURRENT validated
        # emotion name (or None → the neutral middle). The previous turn's state colors the stream
        # while the new one is still generating; the new state takes over the moment it validates
        # (she is still in the mood she was in when she started speaking). Presentation only —
        # voice_settings_for maps it, the same bias the v0.14 voicer uses; the text is untouched.
        self._emotion_supplier = emotion_supplier
        # v1.6.3 LUMI-203: >0 → the turn's FIRST chunk may cut at a clause/word threshold for
        # earlier first audio; 0 (default) → whole-sentence splitting, byte-identical to before.
        self._first_clause_words = max(0, first_clause_words)
        self._filt = StreamTagFilter()
        self._asm = self._new_asm()
        self._lock = threading.Lock()
        self._queue: deque[str] = deque()
        self._epoch = 0
        self._paused = False  # True from interrupt() until resume() — the backlog keeps growing
        # The safety valve: if NOTHING ever calls resume() (Deepgram sent no final/UtteranceEnd for
        # the interrupting noise — seen live as "sound gone forever"), un-pause by timeout. Long
        # enough to never cut into a real spoken utterance (those commit or backstop within ~1-2 s).
        self._auto_resume_s = auto_resume_s
        self._paused_at = 0.0
        self.skipped: list[str] = []  # unspeakable sentences, kept visible for diagnosis

    # --- the reply-stream side -------------------------------------------------------------------
    def feed_delta(self, chunk: str) -> None:
        shown = self._filt.feed(chunk)
        if shown:
            for sentence in self._asm.feed(shown):
                self._enqueue(sentence)

    def finish_turn(self) -> None:
        """End of the reply stream: flush the held tail, reset the filters for the next turn. Does
        NOT touch the pause — that's driven purely by interrupt()/resume(), independent of whether
        a turn succeeded or failed (so a failed turn can never leave audio stuck paused)."""
        tail = self._filt.flush()
        if tail:
            self._asm.feed(tail)
        for sentence in self._asm.flush():
            self._enqueue(sentence)
        self._filt = StreamTagFilter()
        self._asm = self._new_asm()  # re-arms the first-clause cut for the next turn

    def _new_asm(self) -> SentenceAssembler:
        if self._first_clause_words > 0:
            return FirstClauseAssembler(self._first_clause_words)
        return SentenceAssembler()

    def _enqueue(self, sentence: str) -> None:
        cleaned = clean_sentence(sentence)
        if not cleaned:
            return
        if not speakable(cleaned):  # an English/markup leak never reaches her voice
            self.skipped.append(cleaned)
            return
        with self._lock:
            self._queue.append(cleaned)

    # --- the speaking side -----------------------------------------------------------------------
    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._queue)

    @property
    def paused(self) -> bool:
        return self._paused

    def synth_next(self, *, emotion: str | None = None) -> str | None:
        """Stream ONE queued sentence into the speaker buffer; return its text (None when idle OR
        while paused — the backlog waits for :meth:`resume`, it is never dropped).

        An :meth:`interrupt` DURING the stream aborts the remaining chunks (the epoch check) and
        **requeues the sentence at the FRONT** — «вона договорює свій меседж»: the interrupted
        sentence replays from its start after the resume, then the rest follows in order."""
        if self._paused:
            # The watchdog: a pause only resume() never lifted (no final ever came for the
            # interrupting noise) un-sticks itself — silence must never be permanent.
            if time.monotonic() - self._paused_at < self._auto_resume_s:
                return None
            self.resume()
        with self._lock:
            if not self._queue:
                return None
            sentence = self._queue.popleft()
            epoch = self._epoch
        if emotion is None and self._emotion_supplier is not None:
            try:  # LUMI-204: the caller's current state colors this sentence's delivery
                emotion = self._emotion_supplier()
            except Exception:  # noqa: BLE001 — a supplier failure degrades to neutral, never silence
                emotion = None
        for chunk in self._tts.stream(sentence, emotion=emotion):
            with self._lock:
                if self._epoch != epoch:  # barged-in mid-sentence — replay it whole after resume
                    self._queue.appendleft(sentence)
                    return None
            self.buffer.feed(chunk)
        return sentence

    def resume(self) -> None:
        """Lift a barge-in pause — the synth pump resumes speaking the queued backlog in order."""
        with self._lock:
            self._paused = False

    def interrupt(self) -> None:
        """Barge-in: he spoke while she was playing — stop the sound, PAUSE (not cancel). Idempotent.
        The queue is deliberately NOT cleared: nothing she was going to say is skipped — a
        still-streaming turn keeps adding to it, and it all plays once :meth:`resume` lifts the
        pause. Debounce is automatic, not a separate flag: once paused, ``buffer.playing`` is False
        and :meth:`synth_next` refuses to start anything new, so the caller's barge-in check
        (``playing or (not paused and pending)``) naturally stops re-firing for repeated interim
        transcripts during the same burst — the live symptom before this: a rapid interrupt/re-queue
        loop (``[barge-in]`` spam / TUI lag) and content silently discarded instead of queued."""
        with self._lock:
            self._epoch += 1
            self._paused = True
            self._paused_at = time.monotonic()
        self.buffer.clear()

"""The dictator (v0.26) — the voicer's mirror: ``mic → inbox``.

The voicer reads Лілі's replies from ``outbox.jsonl`` and **speaks** them; the dictator **listens to the
mic, recognizes Ukrainian, and writes your line into ``inbox.jsonl``** — the same channel the TUI keyboard
feeds, so the core can't tell typed from dictated. Control is the TUI's: it flips a shared ``listen.flag``
(``on``/``off``) on a key; the dictator records while ``on`` and recognizes on ``off``.

``read_flag`` / ``recognize_and_append`` are pure (unit-tested with a :class:`~voice.stt.MockSTT`);
``run()`` is the glue (lazy mic capture + the STT call; not covered). Run: ``python -m voice.dictator``.
"""

from __future__ import annotations

from pathlib import Path

from state import fifo
from voice.stt import STT

# A recognition shorter than this (after stripping) is treated as empty / low-confidence → dropped,
# never written to the inbox ("better silent than garbage"). 1 = drop only the truly empty result.
MIN_CHARS = 1

_ON_VALUES = {"on", "1", "true", "yes"}


def read_flag(path: str | Path) -> bool:
    """The ``listen.flag`` state — ``True`` when listening. A missing/unreadable flag → ``False`` (off)."""
    p = Path(path)
    if not p.is_file():
        return False
    try:
        return p.read_text(encoding="utf-8").strip().lower() in _ON_VALUES
    except OSError:
        return False


def recognize_and_append(
    audio: bytes,
    inbox_path: str | Path,
    stt: STT,
    *,
    lang: str = "uk",
    min_chars: int = MIN_CHARS,
    log=None,
) -> int | None:
    """Recognize one recording and append your line to ``inbox.jsonl``; return the new id, or ``None``.

    **Empty / low-confidence** recognition (≤ ``min_chars`` after stripping) writes **nothing** and
    returns ``None`` (better silent than garbage). A recognition error degrades the same way (logged,
    never raised). On success the line rides the fifo with ``source="voice"`` and the next monotonic id —
    the **dedup** is the fifo counter + the TUI's inbox pointer (one line, consumed once), exactly like
    the voicer's ``spoken`` pointer.
    """
    try:
        text = (stt.recognize(audio, lang=lang) or "").strip()
    except Exception as exc:  # noqa: BLE001 — a recognition failure degrades to a dropped line, never raises
        if log is not None:
            log.warning("recognition failed (dropped): %s", exc)
        return None
    if len(text) < max(1, min_chars):  # empty / low-confidence → write nothing
        if log is not None:
            log.info("empty / low-confidence recognition — dropped")
        return None
    return fifo.append(inbox_path, text, source="voice")


def run() -> None:  # pragma: no cover - mic capture + STT glue (no audio/paid CI)
    """Watch ``listen.flag``; record the mic while on, recognize on off, append to ``inbox``.

    Requires ``LUMI_DICTATION`` + the chosen STT provider (a key for cloud, or offline Whisper).
    """
    import logging
    import sys
    import time

    from core.config import load_config
    from voice.stt import build_stt

    cfg = load_config()
    if not cfg.dictation:
        raise SystemExit("LUMI_DICTATION is off")

    logging.basicConfig(
        level=getattr(logging, (sys.argv[1:] and sys.argv[1].upper()) or "INFO", logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    log = logging.getLogger("lumi.dictation")
    key = cfg.deepgram_api_key or cfg.elevenlabs_api_key
    stt = build_stt(cfg.stt_provider, api_key=key, model=cfg.stt_model)
    log.info(
        "dictator up: provider=%s, model=%s, lang=%s, flag=%s, inbox=%s",
        cfg.stt_provider, cfg.stt_model or "(default)", cfg.stt_lang, cfg.listen_flag_path, cfg.inbox_path,
    )

    import sounddevice as sd  # lazy: a separate process records; the TUI never captures audio

    samplerate = 16_000
    recording: list = []
    was_on = False
    stream = None
    while True:
        try:
            on = read_flag(cfg.listen_flag_path)
            if on and not was_on:  # off → on: start recording
                recording = []
                stream = sd.InputStream(
                    samplerate=samplerate, channels=1, dtype="int16",
                    callback=lambda indata, *_, _rec=recording: _rec.append(bytes(indata)),
                )
                stream.start()
                log.info("listening…")
            elif not on and was_on:  # on → off: stop + recognize + append
                if stream is not None:
                    stream.stop()
                    stream.close()
                    stream = None
                audio = _wav(b"".join(recording), samplerate)
                rid = recognize_and_append(audio, cfg.inbox_path, stt, lang=cfg.stt_lang, log=log)
                log.info("recognized → inbox id=%s" if rid else "nothing recognized", rid)
            was_on = on
        except Exception as exc:  # noqa: BLE001 — a transient error must not kill the dictator
            log.error("dictator loop error (retrying): %s", exc)
        time.sleep(0.2)


def _wav(pcm: bytes, samplerate: int) -> bytes:  # pragma: no cover - audio glue
    """Wrap raw 16-bit mono PCM in a WAV container (what the STT adapters expect)."""
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(samplerate)
        w.writeframes(pcm)
    return buf.getvalue()


if __name__ == "__main__":  # pragma: no cover
    run()

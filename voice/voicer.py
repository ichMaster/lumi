"""The voicer (v0.14) — daemon 2's twin: ``outbox → speaker``.

Reuses the v0.13 file bus: reads Лілі's replies from ``outbox.jsonl`` via ``state.fifo`` (from a
``spoken`` pointer, the twin of daemon 2's ``outbox.sent``) and **voices only her lines**
(``kind="lili"``) — your mirrored keyboard lines (``kind="user"``) are skipped, never voiced — one at
a time, in order, retrying on failure, and **skipping the pre-existing backlog on first run**.

``voice_pending`` / ``skip_backlog_on_first_run`` are pure (unit-tested with a mock TTS); ``run()`` is
the glue (lazy ``elevenlabs`` + local playback; not covered). Run: ``python -m voice.voicer``.
"""

from __future__ import annotations

from pathlib import Path

from state import fifo
from voice.tts import TTS


def skip_backlog_on_first_run(outbox_path: str | Path, spoken_path: str | Path) -> int | None:
    """First run only (no ``spoken`` pointer yet): set the pointer to the current last outbox id so
    the **pre-existing backlog is not replayed**. Returns the id skipped to, or ``None`` when resuming.
    """
    if Path(spoken_path).is_file():
        return None  # resuming — keep the saved pointer
    records = fifo.read_since(outbox_path, 0)
    last = records[-1]["id"] if records else 0
    fifo.save_pointer(spoken_path, last)
    return last


def voice_pending(outbox_path: str | Path, spoken_path: str | Path, tts: TTS, play) -> int:
    """Voice the unspoken ``kind="lili"`` replies, ascending, **one at a time**; return the count.

    ``kind="user"`` records are skipped (the pointer still advances past them). On a synth/playback
    **failure**, stop **without** advancing past the failed id (leave the pointer before it) — so it
    is retried next loop, never lost or repeated.
    """
    voiced = 0
    for rec in fifo.read_since(outbox_path, fifo.load_pointer(spoken_path)):
        if rec.get("kind") != "user":  # voice her lines; never your mirrored keyboard lines
            try:
                play(tts.synth(rec["text"], emotion=rec.get("emotion")))
            except Exception:  # noqa: BLE001 — leave the pointer before this id → retry next loop
                break
            voiced += 1
        fifo.save_pointer(spoken_path, rec["id"])  # advance past this record (voiced or skipped-user)
    return voiced


def run() -> None:  # pragma: no cover - cloud TTS + local playback glue (no paid CI)
    """Poll the outbox and voice new replies via ElevenLabs. Requires ``LUMI_VOICE`` + the key/voice."""
    import logging
    import sys
    import time

    from core.config import load_config
    from voice.tts import ElevenLabsTTS

    cfg = load_config()
    if not cfg.voice:
        raise SystemExit("LUMI_VOICE is off")
    if not cfg.elevenlabs_api_key:
        raise SystemExit("ELEVENLABS_API_KEY is not set")
    if not cfg.voice_id:
        raise SystemExit("LUMI_VOICE_ID is not set")

    logging.basicConfig(
        level=getattr(logging, (sys.argv[1:] and sys.argv[1].upper()) or "INFO", logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    log = logging.getLogger("lumi.voice")
    tts = ElevenLabsTTS(cfg.elevenlabs_api_key, cfg.voice_id, cfg.voice_model)
    spoken = cfg.outbox_path.with_suffix(".spoken")

    def play(audio: bytes) -> None:
        from elevenlabs import play as el_play  # lazy

        el_play(audio)

    skipped = skip_backlog_on_first_run(cfg.outbox_path, spoken)
    if skipped is not None:
        log.info("first run: skipping backlog, starting from id=%d", skipped)
    log.info("voicer up: voice=%s, model=%s, outbox=%s", cfg.voice_id, cfg.voice_model, cfg.outbox_path)
    while True:
        try:
            n = voice_pending(cfg.outbox_path, spoken, tts, play)
            if n:
                log.info("voiced %d reply(ies)", n)
        except Exception as exc:  # noqa: BLE001 — a transient error must not kill the voicer
            log.error("voicer loop error (retrying): %s", exc)
        time.sleep(1)


if __name__ == "__main__":  # pragma: no cover
    run()

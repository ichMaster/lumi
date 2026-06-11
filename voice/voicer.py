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


def voice_pending(outbox_path: str | Path, spoken_path: str | Path, tts: TTS, play, *, log=None) -> int:
    """Voice the unspoken ``kind="lili"`` replies, ascending, **one at a time**; return the count.

    ``kind="user"`` records are skipped (the pointer still advances past them). The two failure modes
    differ on purpose:

    - **synth failure** (network) → **stop without advancing** (leave the pointer before this id), so it
      retries next loop — nothing lost or repeated;
    - **playback failure** (after a successful synth) → **log and advance** — the audio was already
      synthesized, so re-synthesizing would burn TTS credits on a stuck speaker; skip it instead.
    """
    voiced = 0
    for rec in fifo.read_since(outbox_path, fifo.load_pointer(spoken_path)):
        if rec.get("kind") != "user":  # voice her lines; never your mirrored keyboard lines
            try:
                audio = tts.synth(rec["text"], emotion=rec.get("emotion"))
            except Exception as exc:  # noqa: BLE001 — synth (network) failed → retry: leave the pointer, stop
                if log is not None:
                    log.warning("synth failed for id=%s (retrying): %s", rec.get("id"), exc)
                break
            try:
                play(audio)
                voiced += 1
            except Exception as exc:  # noqa: BLE001 — playback failed AFTER synth → skip (do NOT re-synthesize)
                if log is not None:
                    log.error("playback failed for id=%s (skipping, not re-synthesizing): %s", rec.get("id"), exc)
        fifo.save_pointer(spoken_path, rec["id"])  # advance past this record (voiced / user / play-failed)
    return voiced


def play_audio(audio: bytes) -> None:  # pragma: no cover - subprocess + an audio device
    """Play MP3 bytes through a system player (``afplay`` on macOS, else ``ffplay``), blocking.

    The ElevenLabs SDK's own ``play`` is unreliable across versions (``from elevenlabs import play`` may
    resolve to a module), so the voicer plays the audio itself — one reply at a time (blocking).
    """
    import os
    import shutil
    import subprocess
    import tempfile

    afplay, ffplay = shutil.which("afplay"), shutil.which("ffplay")
    if not (afplay or ffplay):
        raise RuntimeError("no audio player found — install ffmpeg (ffplay), or use macOS afplay")
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(audio)
        path = f.name
    try:
        cmd = [afplay, path] if afplay else [ffplay, "-autoexit", "-nodisp", "-loglevel", "quiet", path]
        subprocess.run(cmd, check=True)
    finally:
        os.unlink(path)


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
    logging.getLogger("httpx").setLevel(logging.WARNING)  # quiet the per-synth HTTP 200 lines
    tts = ElevenLabsTTS(cfg.elevenlabs_api_key, cfg.voice_id, cfg.voice_model)
    spoken = cfg.outbox_path.with_suffix(".spoken")

    skipped = skip_backlog_on_first_run(cfg.outbox_path, spoken)
    if skipped is not None:
        log.info("first run: skipping backlog, starting from id=%d", skipped)
    log.info("voicer up: voice=%s, model=%s, outbox=%s", cfg.voice_id, cfg.voice_model, cfg.outbox_path)
    while True:
        try:
            n = voice_pending(cfg.outbox_path, spoken, tts, play_audio, log=log)
            if n:
                log.info("voiced %d reply(ies)", n)
        except Exception as exc:  # noqa: BLE001 — a transient error must not kill the voicer
            log.error("voicer loop error (retrying): %s", exc)
        time.sleep(1)


if __name__ == "__main__":  # pragma: no cover
    run()

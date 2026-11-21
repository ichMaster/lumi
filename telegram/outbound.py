"""Daemon 2 — ``outbox → telegram`` (v0.13, LUMI-056).

Forward Лілі's ``outbox`` records to Telegram, **FIFO from the pointer**, consolidating up to
``LUMI_TELEGRAM_BATCH`` = N consecutive records per message (so a backlog becomes ⌈M/N⌉ messages,
**never** "the last several days as one"). A **catch-up cap** skips records older than
``LUMI_TELEGRAM_CATCHUP_H`` on restart (so a long downtime doesn't flood). Each reply carries its
**emoji**; optionally the flat **face portrait** rides as a photo.

v4.6: her voice is made **on the server**, once per line. A record that names a stored file
(``voice: "<id>.mp3"`` under ``<data root>/voice/<user_id>/``) goes as a voice message from that file — the daemon
never calls ElevenLabs for it; ``voice: null`` goes as text; a record without the key (written before v4.6, or by the
in-process TUI) goes the old way — voiced by the daemon's own TTS in its voice mode (``telegram.voice``), else text.
:func:`forward` is one pass over the outbox, decided per record (:func:`delivery`, :func:`plan`).

The planning + rendering are pure and unit-tested; ``run()`` is the ``aiogram`` glue (lazy import;
not covered).
"""

from __future__ import annotations

import random
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from core.emoji import BUILTIN, emoji_for
from core.emotion import DEFAULT_EMOTION, Emotion, EmotionState
from core.stopsignal import sigterm_as_interrupt
from state import fifo
from viewer.face import face_for

# Telegram hard limits (a caption is much tighter than a message — the source of the photo gotcha).
CAPTION_LIMIT = 1024
MESSAGE_LIMIT = 4096
USER_PREFIX = "💻"  # marks a line you typed in the TUI (mirrored to Telegram), vs Лілі's own reply
VOICE_NAME_RE = re.compile(r"[0-9a-f]{32}\.mp3")  # v4.6: a stored voice file's name on an outbox record


def split_catchup(records: list[dict], now: datetime, catchup_h: int) -> tuple[list[dict], list[dict]]:
    """Split records into ``(stale, fresh)`` by age — stale = `ts` older than ``now - catchup_h``.

    Records are appended in time order, so the stale ones are the oldest prefix (skipped silently on
    restart). An unparseable/missing ``ts`` counts as fresh (never silently dropped).
    """
    cutoff = now - timedelta(hours=catchup_h)
    stale: list[dict] = []
    fresh: list[dict] = []
    for r in records:
        try:
            ts = datetime.fromisoformat(r["ts"])
        except (ValueError, KeyError, TypeError):
            ts = now
        (stale if ts < cutoff else fresh).append(r)
    return stale, fresh


def is_photo_record(record: dict) -> bool:
    """True if a record is a v0.24 **chosen `send_image`** — it carries a non-empty ``photo`` path."""
    return bool(record.get("photo"))


def batches(records: list[dict], n: int) -> list[list[dict]]:
    """Group consecutive records into chunks of at most ``n`` (bounds a backlog → ⌈M/n⌉ messages).

    A v0.24 **photo** record (a chosen `send_image`) is always its **own** group — a picture she chose to
    send goes out on its own, never N-batched with replies; so is a v4.6 record with a stored voice file. Text records
    around them batch as before. (The groups of :func:`plan`, outside voice mode.)
    """
    return [group for _, group in plan(records, n)]


def _glyph(record: dict, emoji_map: dict) -> str:
    """The emoji for a record's emotion+intensity (unknown emotion → the calm default)."""
    try:
        emotion = Emotion(str(record.get("emotion", DEFAULT_EMOTION.value)))
    except ValueError:
        emotion = DEFAULT_EMOTION
    intensity = float(record.get("intensity", 0.5) or 0.5)
    return emoji_for(EmotionState(reply="", emotion=emotion, intensity=intensity), emoji_map)


def render(records: list[dict], emoji_map: dict | None = None) -> str:
    """One Telegram message text from a batch — blank-line separated.

    A ``kind="user"`` record is **your** mirrored TUI line (prefixed, no emoji); everything else is
    Лілі's reply (text + her emoji).
    """
    table = emoji_map or BUILTIN
    lines: list[str] = []
    for r in records:
        if r.get("kind") == "user":
            lines.append(f"{USER_PREFIX} {r['text']}".strip())
        else:
            lines.append(f"{r['text']} {_glyph(r, table)}".strip())
    return "\n\n".join(lines)


def chunk(text: str, limit: int) -> list[str]:
    """Split ``text`` into pieces of at most ``limit`` chars, preferring newline/space boundaries.

    Guards the Telegram message/caption length caps so an over-long reply (or a big N-batch) can't
    wedge the daemon. A short string passes through unchanged.
    """
    if len(text) <= limit:
        return [text]
    pieces: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        if cut <= 0:
            cut = rest.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit  # no boundary — hard cut
        pieces.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        pieces.append(rest)
    return pieces


def portrait_for(faces_dir: str | Path, emotion: str, intensity: float | None = None) -> Path | None:
    """The flat v0.7 ``faces/<emotion>.png`` for a reply, or ``None`` if no face file exists.

    The daemon has no Core/mood, so it uses the **flat** (theme-less) faces; absent → text-only.
    """
    p = face_for(emotion, intensity, faces_dir)
    return p if p.is_file() else None


# ── Voice mode (LUMI_TELEGRAM_VOICE) ──────────────────────────────────────────────────────────────
def caption_for(rec: dict, emoji_map: dict | None = None) -> str:
    """The words for a voice message — the reply text + its emoji, whole. :func:`forward` sends them as the caption
    when they fit one, else as text after the voice (review #2: never cut at the caption cap)."""
    return render([rec], emoji_map)


def mp3_to_ogg(mp3: bytes) -> bytes:  # pragma: no cover - ffmpeg subprocess
    """Convert MP3 bytes → OGG/OPUS bytes (what a Telegram voice message wants) via ffmpeg."""
    import subprocess

    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-c:a", "libopus", "-b:a", "32k", "-f", "ogg", "pipe:1"],
        input=mp3, capture_output=True, check=True,
    )
    return out.stdout


def delivery(record: dict, *, voice_mode: bool = False) -> str:
    """How one outbox record goes (v4.6): ``"photo"`` (a chosen ``send_image``), ``"stored"`` (her line with the
    server's voice file — a well-formed name only), ``"old"`` (her line from before v4.6 or the in-process TUI, voiced
    by the daemon in its voice mode), ``"skip"`` (your mirrored line in voice mode — never sent, as before v4.6) or
    ``"text"``."""
    if is_photo_record(record):
        return "photo"
    if record.get("kind") == "user":
        return "skip" if voice_mode else "text"
    if "voice" in record:
        name = record.get("voice")
        return "stored" if isinstance(name, str) and VOICE_NAME_RE.fullmatch(name) else "text"
    return "old" if voice_mode else "text"


def plan(records: list[dict], n: int, *, voice_mode: bool = False) -> list[tuple[str, list[dict]]]:
    """The pass's sends in FIFO order: a photo, a voice message or a skipped line stands alone; the text records
    between them are grouped by at most ``n`` (bounds a backlog → ⌈M/n⌉ messages)."""
    n = max(1, n)
    out: list[tuple[str, list[dict]]] = []
    run: list[dict] = []
    for record in records:
        kind = delivery(record, voice_mode=voice_mode)
        if kind == "text":
            run.append(record)
            continue
        if run:
            out += [("text", run[i : i + n]) for i in range(0, len(run), n)]
            run = []
        out.append((kind, [record]))
    if run:
        out += [("text", run[i : i + n]) for i in range(0, len(run), n)]
    return out


def stored_voice(record: dict, voice_dir: Path) -> bytes | None:
    """The MP3 the server stored for a record (``voice_dir / record["voice"]``), or ``None`` — a bad name, a link,
    missing (expired) or unreadable."""
    name = record.get("voice")
    if not isinstance(name, str) or not VOICE_NAME_RE.fullmatch(name):
        return None
    path = Path(voice_dir) / name
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


async def forward(outbox_path, sent_path, *, batch: int, voice_dir: Path,
                  send_text: Callable[[list[dict]], Awaitable[None]],
                  send_photo: Callable[[dict], Awaitable[None]],
                  send_voice: Callable[[bytes, str], Awaitable[None]],
                  to_ogg: Callable[[bytes], bytes], caption_for: Callable[[dict], str],
                  tts: Any = None, voice_mode: bool = False, log: Any = None,
                  ledger: Path | None = None) -> tuple[int, bool]:
    """One pass over the new outbox records (v4.6) → ``(records handled, whether a send failed)``.

    v5.1 (LUMI-341): an old record voiced here writes one ``tts`` record to ``ledger`` (the media ledger): the
    characters voiced, the model, the outcome — origin ``housekeeping:voice``, channel ``telegram``; never the text.

    Per :func:`plan`: a stored voice file is converted (``to_ogg``) and sent with its caption — **never a TTS call**; a
    missing or unconvertible file degrades to text. An old record is voiced by ``tts`` (its ``speech`` before its
    ``text``) — only in voice mode, with a ``tts``. ``caption_for`` gives her whole words: past ``CAPTION_LIMIT`` the
    voice goes without a caption and the words follow as text (``send_text``, chunked there). A send failure stops the
    pass **without** moving the pointer past that send (the next pass retries it); every handled send moves it."""
    def note(level: str, msg: str, *args: Any) -> None:
        if log is not None:
            getattr(log, level)(msg, *args)

    async def voice_and_words(ogg: bytes, group: list[dict]) -> None:
        words = caption_for(group[-1])
        if len(words) <= CAPTION_LIMIT:
            await send_voice(ogg, words)
        else:  # review #2: a caption would cut her words — the voice alone, then the words in full
            await send_voice(ogg, "")
            await send_text(group)

    handled = 0
    records = fifo.read_since(outbox_path, fifo.load_pointer(sent_path))
    for kind, group in plan(records, batch, voice_mode=voice_mode and tts is not None):
        last = group[-1]
        try:
            if kind == "photo":
                await send_photo(last)
            elif kind == "stored":
                mp3 = stored_voice(last, voice_dir)
                ogg = None
                if mp3 is not None:
                    try:
                        ogg = to_ogg(mp3)
                    except Exception as exc:  # noqa: BLE001 — a file that won't convert never will: send the words
                        note("warning", "stored voice id=%s can't be converted (%s) — sent as text", last["id"], exc)
                else:
                    note("warning", "stored voice id=%s is missing — sent as text", last["id"])
                if ogg is None:
                    await send_text(group)
                else:
                    await voice_and_words(ogg, group)
                    note("info", "sent the stored voice id=%s", last["id"])
            elif kind == "old":
                ogg = to_ogg(_voiced_old_way(tts, last, ledger))
                await voice_and_words(ogg, group)
                note("info", "voiced the old way id=%s", last["id"])
            elif kind == "text":
                await send_text(group)
            # "skip": your mirrored line in voice mode — never sent; the pointer moves past it
        except Exception as exc:  # noqa: BLE001 — leave the pointer before this send → retry next pass
            note("error", "send failed (ids %s..%s, retrying): %s", group[0]["id"], last["id"], exc)
            return handled, True
        fifo.save_pointer(sent_path, last["id"])
        handled += len(group)
    return handled, False


def _voiced_old_way(tts: Any, record: dict, ledger: Path | None) -> bytes:
    """One TTS call for an old record (``speech`` before ``text``), recorded in the media ledger whatever its end."""
    import time

    spoken = record.get("speech") or record["text"]
    started, outcome = time.monotonic(), "ok"
    try:
        return tts.synth(spoken, emotion=record.get("emotion"))
    except Exception as exc:
        outcome = f"error:{type(exc).__name__}"
        raise
    finally:
        if ledger is not None:
            try:
                from core import ledgers

                ledgers.append_media(ledger, ledgers.media_record(
                    provider="elevenlabs", service="tts", model=str(getattr(tts, "model", "") or ""),
                    units={"chars": len(spoken)}, latency_ms=int((time.monotonic() - started) * 1000),
                    outcome=outcome, origin="housekeeping:voice", channel="telegram"))
            except Exception:  # noqa: BLE001 — recording never stops a send
                pass


async def send_photo_record(bot, chats, record, *, emoji_map=None, fs_input=None, log=None) -> None:
    """Send ONE v0.24 **chosen `send_image`** record as a Telegram **photo** — always a photo (not
    ``LUMI_TELEGRAM_PHOTO``-gated, that stays the random *face*), on its own (not N-batched).

    Caption = the record's text + emoji, caption-capped; an over-long caption sends the photo first, then
    the text in pieces. A **missing/unreadable** file degrades to sending just the words (never wedges
    the loop). ``fs_input`` wraps a path for aiogram (``FSInputFile``); tests pass ``str`` + a fake bot.
    On a send failure it raises (the caller leaves the pointer → retry), like the text path.
    """
    wrap = fs_input or (lambda p: p)
    photo = Path(record.get("photo", "") or "")
    text = render([record], emoji_map)  # her words + emoji (full — NOT pre-capped, so we can decide below)
    for chat in chats:
        if photo.is_file():
            if len(text) <= CAPTION_LIMIT:
                await bot.send_photo(chat, wrap(str(photo)), caption=text)
            else:  # text too long for a caption → photo, then the words as message(s)
                await bot.send_photo(chat, wrap(str(photo)))
                for piece in chunk(text, MESSAGE_LIMIT):
                    await bot.send_message(chat, piece)
        else:  # the file vanished between the turn and the send — send the words, don't crash
            if log is not None:
                log.warning("send_image file missing (id=%s): %s", record.get("id"), photo)
            for piece in chunk(text, MESSAGE_LIMIT):
                await bot.send_message(chat, piece)


def run() -> None:  # pragma: no cover - aiogram glue (network, no paid CI)
    """Forward new ``outbox`` records to the allowlisted chat(s): catch-up skip, then FIFO N-batches."""
    import asyncio

    from aiogram import Bot
    from aiogram.types import FSInputFile

    from core.config import load_config
    from core.emoji import load_emoji_map

    from . import get_logger

    cfg = load_config()
    from core.envguard import guard_entry

    guard_entry(cfg)  # v2.1: refuse before touching the data root (dev ↛ prod data, prod = released code)
    if not cfg.telegram_token:
        raise SystemExit("LUMI_TELEGRAM_TOKEN is not set")
    if not cfg.telegram_allowlist:
        raise SystemExit("LUMI_TELEGRAM_ALLOWLIST is empty — no chat to send to")

    log = get_logger("outbound", cfg.outbox_path.parent, level=cfg.log_level)
    emoji_map = load_emoji_map(cfg.emoji_path)
    sent_path = cfg.outbox_path.with_suffix(".sent")
    bot = Bot(cfg.telegram_token)
    chats = cfg.telegram_allowlist

    from aiogram.types import BufferedInputFile

    from core.user import DEFAULT_USER_ID

    voice_dir = cfg.data_root / "voice" / DEFAULT_USER_ID  # v4.6: the server's voice files (single-owner)
    tts = None
    if cfg.telegram_voice:  # the v0.13 voice mode: only for records written before v4.6 (or by the in-process TUI)
        if cfg.elevenlabs_api_key and cfg.voice_id:
            from voice.tts import ElevenLabsTTS

            tts = ElevenLabsTTS(cfg.elevenlabs_api_key, cfg.voice_id, cfg.voice_model)
        else:
            log.warning("telegram.voice is on without an ElevenLabs key + voice id — old records go as text")

    async def _send_voice(ogg: bytes, caption: str) -> None:
        for chat in chats:
            await bot.send_voice(chat, BufferedInputFile(ogg, "voice.ogg"), caption=caption or None)

    async def _send_photo(rec: dict) -> None:  # v0.24: a chosen send_image — always a photo, on its own
        await send_photo_record(bot, chats, rec, emoji_map=emoji_map, fs_input=FSInputFile, log=log)
        log.info("sent a chosen image (id %d) → %d chat(s)", rec["id"], len(chats))

    async def _send_text(group: list[dict]) -> None:
        last = group[-1]
        text = render(group, emoji_map)
        # send the face photo with probability LUMI_TELEGRAM_PHOTO (0=never, 0.2≈1/5, 1=always)
        photo = portrait_for(cfg.faces_dir, last.get("emotion", ""), last.get("intensity")) \
            if random.random() < cfg.telegram_photo else None
        for chat in chats:
            # photo+caption only when the text fits a caption; otherwise chunked text
            # (the photo is decoration — a long reply must never wedge the send).
            if photo and len(text) <= CAPTION_LIMIT:
                await bot.send_photo(chat, FSInputFile(photo), caption=text)
            else:
                for piece in chunk(text, MESSAGE_LIMIT):
                    await bot.send_message(chat, piece)
        log.info("sent %d record(s) as text (ids %d..%d) → %d chat(s)", len(group), group[0]["id"], last["id"],
                 len(chats))

    log.info(
        "outbound up: voice mode=%s (old records), batch=%d, catchup=%dh, photo=%s, chats=%s",
        cfg.telegram_voice, cfg.telegram_batch, cfg.telegram_catchup_h, cfg.telegram_photo, sorted(chats),
    )
    if cfg.env:  # v2.1: which instance this daemon serves (prod/dev) — no secrets, no text
        log.info("env=%s version=%s", cfg.env, cfg.app_version or "?")

    async def _main() -> None:
        if not sent_path.is_file():
            # FIRST run ever: don't replay history — start from the current tail. (The time-based
            # catch-up can't protect a same-day backlog; a fresh daemon should never flood.)
            existing = fifo.read_since(cfg.outbox_path, 0)
            if existing:
                fifo.save_pointer(sent_path, existing[-1]["id"])
                log.info("first run: skipping %d pre-existing record(s), starting from id=%d",
                         len(existing), existing[-1]["id"])
        else:
            # RESUME: skip records gone stale during a downtime (the catch-up cap).
            backlog = fifo.read_since(cfg.outbox_path, fifo.load_pointer(sent_path))
            stale, _ = split_catchup(backlog, datetime.now(UTC), cfg.telegram_catchup_h)
            if stale:
                fifo.save_pointer(sent_path, stale[-1]["id"])
                log.info("catch-up: skipped %d stale record(s) (older than %dh)", len(stale), cfg.telegram_catchup_h)
        while True:
            _, failed = await forward(
                cfg.outbox_path, sent_path, batch=cfg.telegram_batch, voice_dir=voice_dir,
                send_text=_send_text, send_photo=_send_photo, send_voice=_send_voice, to_ogg=mp3_to_ogg,
                caption_for=lambda r: caption_for(r, emoji_map), tts=tts, voice_mode=cfg.telegram_voice, log=log,
                ledger=cfg.data_root / "media-ledger.jsonl",  # v5.1: an old record's voice is recorded
            )
            await asyncio.sleep(3 if failed else 1)

    sigterm_as_interrupt()  # v2.6: `systemctl stop` stops it like Ctrl+C — "stopped", the session closed, exit 0
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        log.info("outbound stopped")
    finally:
        asyncio.run(bot.session.close())


if __name__ == "__main__":  # pragma: no cover
    run()

"""Daemon 2 — ``outbox → telegram`` (v0.13, LUMI-056).

Forward Лілі's ``outbox`` records to Telegram, **FIFO from the pointer**, consolidating up to
``LUMI_TELEGRAM_BATCH`` = N consecutive records per message (so a backlog becomes ⌈M/N⌉ messages,
**never** "the last several days as one"). A **catch-up cap** skips records older than
``LUMI_TELEGRAM_CATCHUP_H`` on restart (so a long downtime doesn't flood). Each reply carries its
**emoji**; optionally the flat **face portrait** rides as a photo.

The planning + rendering are pure and unit-tested; ``run()`` is the ``aiogram`` glue (lazy import;
not covered).
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.emoji import BUILTIN, emoji_for
from core.emotion import DEFAULT_EMOTION, Emotion, EmotionState
from state import fifo
from viewer.face import face_for

# Telegram hard limits (a caption is much tighter than a message — the source of the photo gotcha).
CAPTION_LIMIT = 1024
MESSAGE_LIMIT = 4096
USER_PREFIX = "💻"  # marks a line you typed in the TUI (mirrored to Telegram), vs Лілі's own reply


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
    send goes out on its own, never N-batched with replies. Text records around it batch as before.
    """
    n = max(1, n)
    groups: list[list[dict]] = []
    run: list[dict] = []
    for r in records:
        if is_photo_record(r):
            if run:
                groups += [run[i : i + n] for i in range(0, len(run), n)]
                run = []
            groups.append([r])  # the chosen photo, on its own
        else:
            run.append(r)
    if run:
        groups += [run[i : i + n] for i in range(0, len(run), n)]
    return groups


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
    """The caption for a voice message — the reply text + its emoji, truncated to the caption cap."""
    return render([rec], emoji_map)[:CAPTION_LIMIT]


def mp3_to_ogg(mp3: bytes) -> bytes:  # pragma: no cover - ffmpeg subprocess
    """Convert MP3 bytes → OGG/OPUS bytes (what a Telegram voice message wants) via ffmpeg."""
    import subprocess

    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-c:a", "libopus", "-b:a", "32k", "-f", "ogg", "pipe:1"],
        input=mp3, capture_output=True, check=True,
    )
    return out.stdout


async def voice_to_telegram(outbox_path, sent_path, *, tts, to_ogg, send_voice, caption_for,
                            send_photo=None, log=None) -> int:
    """Send each new ``kind="lili"`` reply as a Telegram **voice message** (one per reply); return count.

    Skips ``kind="user"`` (advances the pointer past them). ``synth → to_ogg → send_voice(ogg, caption)``
    per reply; on a synth/convert/send **failure**, stop **without** advancing (retry next loop) — the
    same resilience as the text path. ``send_voice`` is awaited; ``tts`` / ``to_ogg`` / ``caption_for``
    are injected so this is testable with a mock TTS + a fake bot (no network).

    A **v0.24 chosen `send_image`** record (it carries a ``photo``) is **always sent as a photo**, even in
    voice mode — voice mode governs how her *spoken replies* are delivered, not whether a deliberately-sent
    picture goes. When ``send_photo`` (an injected ``async (record) -> None``) is given, such a record is
    routed there instead of being voiced; without it (legacy callers) the record is voiced as before.
    """
    count = 0
    for rec in fifo.read_since(outbox_path, fifo.load_pointer(sent_path)):
        try:
            if send_photo is not None and is_photo_record(rec):  # a chosen image → always a photo
                await send_photo(rec)
                count += 1
            elif rec.get("kind") != "user":  # voice her lines; never your mirrored keyboard lines
                ogg = to_ogg(tts.synth(rec["text"], emotion=rec.get("emotion")))
                await send_voice(ogg, caption_for(rec))
                count += 1
        except Exception as exc:  # noqa: BLE001 — leave the pointer before this id → retry next loop
            if log is not None:
                log.error("voice/photo send failed for id=%s (retrying): %s", rec.get("id"), exc)
            break
        fifo.save_pointer(sent_path, rec["id"])
    return count


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
    if not cfg.telegram_token:
        raise SystemExit("LUMI_TELEGRAM_TOKEN is not set")
    if not cfg.telegram_allowlist:
        raise SystemExit("LUMI_TELEGRAM_ALLOWLIST is empty — no chat to send to")

    log = get_logger("outbound", cfg.outbox_path.parent)
    emoji_map = load_emoji_map(cfg.emoji_path)
    sent_path = cfg.outbox_path.with_suffix(".sent")
    bot = Bot(cfg.telegram_token)
    chats = cfg.telegram_allowlist

    tts = None
    send_voice = None
    if cfg.telegram_voice:  # voice mode reuses the v0.14 TTS adapter + key/voice
        from aiogram.types import BufferedInputFile

        from voice.tts import ElevenLabsTTS

        if not (cfg.elevenlabs_api_key and cfg.voice_id):
            raise SystemExit("LUMI_TELEGRAM_VOICE needs ELEVENLABS_API_KEY + LUMI_VOICE_ID")
        tts = ElevenLabsTTS(cfg.elevenlabs_api_key, cfg.voice_id, cfg.voice_model)

        async def send_voice(ogg: bytes, caption: str) -> None:
            for chat in chats:
                await bot.send_voice(chat, BufferedInputFile(ogg, "voice.ogg"), caption=caption)

    async def _send_photo(rec: dict) -> None:  # v0.24: a chosen send_image — a photo even in voice mode
        await send_photo_record(bot, chats, rec, emoji_map=emoji_map, fs_input=FSInputFile, log=log)

    log.info(
        "outbound up: voice=%s, batch=%d, catchup=%dh, photo=%s, chats=%s",
        cfg.telegram_voice, cfg.telegram_batch, cfg.telegram_catchup_h, cfg.telegram_photo, sorted(chats),
    )

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
            if cfg.telegram_voice:  # VOICE mode: one voice message per reply (no batching)
                n = await voice_to_telegram(
                    cfg.outbox_path, sent_path, tts=tts, to_ogg=mp3_to_ogg,
                    send_voice=send_voice, caption_for=lambda r: caption_for(r, emoji_map),
                    send_photo=_send_photo, log=log,
                )
                if n:
                    log.info("voiced/sent %d reply(ies) → %d chat(s)", n, len(chats))
                await asyncio.sleep(1)
                continue
            new = fifo.read_since(cfg.outbox_path, fifo.load_pointer(sent_path))
            for group in batches(new, cfg.telegram_batch):
                last = group[-1]
                if len(group) == 1 and is_photo_record(group[0]):  # v0.24: a chosen send_image — on its own
                    try:
                        await send_photo_record(
                            bot, chats, group[0], emoji_map=emoji_map, fs_input=FSInputFile, log=log,
                        )
                    except Exception as exc:  # noqa: BLE001 — don't advance the pointer; retry next loop
                        log.error("photo send failed (id %d, retrying): %s", last["id"], exc)
                        await asyncio.sleep(3)
                        break
                    fifo.save_pointer(sent_path, last["id"])
                    log.info("sent a chosen image (id %d) → %d chat(s)", last["id"], len(chats))
                    continue
                text = render(group, emoji_map)
                # send the face photo with probability LUMI_TELEGRAM_PHOTO (0=never, 0.2≈1/5, 1=always)
                photo = portrait_for(cfg.faces_dir, last.get("emotion", ""), last.get("intensity")) \
                    if random.random() < cfg.telegram_photo else None
                try:
                    for chat in chats:
                        # photo+caption only when the text fits a caption; otherwise chunked text
                        # (the photo is decoration — a long reply must never wedge the send).
                        if photo and len(text) <= CAPTION_LIMIT:
                            await bot.send_photo(chat, FSInputFile(photo), caption=text)
                        else:
                            for piece in chunk(text, MESSAGE_LIMIT):
                                await bot.send_message(chat, piece)
                except Exception as exc:  # noqa: BLE001 — don't advance the pointer; retry next loop
                    log.error("send failed (ids %d..%d, retrying): %s", group[0]["id"], last["id"], exc)
                    await asyncio.sleep(3)
                    break
                fifo.save_pointer(sent_path, last["id"])
                log.info("sent %d reply(ies) (ids %d..%d) → %d chat(s)", len(group), group[0]["id"], last["id"], len(chats))
            await asyncio.sleep(1)

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        log.info("outbound stopped")
    finally:
        asyncio.run(bot.session.close())


if __name__ == "__main__":  # pragma: no cover
    run()

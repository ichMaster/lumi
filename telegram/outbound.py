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


def batches(records: list[dict], n: int) -> list[list[dict]]:
    """Group consecutive records into chunks of at most ``n`` (bounds a backlog → ⌈M/n⌉ messages)."""
    n = max(1, n)
    return [records[i : i + n] for i in range(0, len(records), n)]


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
    log.info(
        "outbound up: batch=%d, catchup=%dh, photo=%s, chats=%s",
        cfg.telegram_batch, cfg.telegram_catchup_h, cfg.telegram_photo, sorted(chats),
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
            new = fifo.read_since(cfg.outbox_path, fifo.load_pointer(sent_path))
            for group in batches(new, cfg.telegram_batch):
                text = render(group, emoji_map)
                last = group[-1]
                photo = portrait_for(cfg.faces_dir, last.get("emotion", ""), last.get("intensity")) \
                    if cfg.telegram_photo else None
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

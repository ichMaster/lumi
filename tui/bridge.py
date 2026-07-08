"""TUI ↔ peripheral bridge helpers (v0.13) — drain the `inbox` FIFO, mirror Лілі's replies to `outbox`.

**No Textual dependency** — pure functions over the file bus (`state.fifo`), so they're unit-testable
without the app. The TUI is the inbox **consumer** (reads new lines on idle → runs each as a turn) and
the outbox **producer** (appends **only Лілі's own replies** — never your input → the bridge is
**echo-free by construction**). The Telegram daemons (v0.13) and later voice/dictation share this bus.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from core.emotion import EmotionState
from state import fifo


def drain_inbox_records(inbox_path: str | Path, pos_path: str | Path) -> list[dict]:
    """The unread `inbox` **records** (oldest first); advances the pointer past them. Empty when caught up.

    Each record carries its ``source`` (``"voice"`` for a v0.26 dictated line, else a Telegram line), so
    the consumer can tag it; :func:`drain_inbox` is the text-only convenience over this.
    """
    records = fifo.read_since(inbox_path, fifo.load_pointer(pos_path))
    if records:
        fifo.save_pointer(pos_path, records[-1]["id"])
    return records


def drain_inbox(inbox_path: str | Path, pos_path: str | Path) -> list[str]:
    """The unread `inbox` lines (oldest first); advances the pointer past them. Empty when caught up."""
    return [r["text"] for r in drain_inbox_records(inbox_path, pos_path)]


def set_listen_flag(flag_path: str | Path, on: bool) -> None:
    """Write the v0.26 dictation `listen.flag` (``on``/``off``). The TUI is the **only** writer; the
    dictator process reads it to know when to record. Best-effort — the parent dir is created."""
    p = Path(flag_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("on" if on else "off", encoding="utf-8")


def mirror_reply(outbox_path: str | Path, state: EmotionState) -> int:
    """Append Лілі's reply (``kind="lili"`` + her emotion) to the `outbox`; return the new id.

    Called once per turn at the point her reply is shown — keyboard, Telegram, or a spoken proactive
    thought alike.
    """
    return fifo.append(
        outbox_path, state.reply, kind="lili",
        emotion=state.emotion.value, intensity=state.intensity,
    )


def mirror_thought(outbox_path: str | Path, text: str, emotion: str = "calm", intensity: float = 0.5) -> int:
    """Append a **surfaced proactive thought** to the outbox as a Лілі message (v0.42) — so a scheduled
    `show`/`%name!` thought reaches Telegram + the voicer, not just the TUI. ``kind="lili"`` like a reply;
    the 💭 marker stays TUI-only (the outbox carries the clean text)."""
    return fifo.append(outbox_path, text, kind="lili", emotion=emotion, intensity=intensity)


def mirror_user(outbox_path: str | Path, text: str) -> int:
    """Append **your keyboard message** (``kind="user"``) to the `outbox` so it shows in Telegram too.

    Only for **keyboard** turns — a Telegram-originated message is *not* passed here (it's already in
    Telegram; re-sending it would echo). This is the symmetric half of the `📱` inbox line in the TUI.
    """
    return fifo.append(outbox_path, text, kind="user")


def mirror_photo(outbox_path: str | Path, abs_path: str, caption: str) -> int:
    """Append a Telegram **photo** record (``kind="lili"`` + a ``photo`` field) — the v0.24 `send_image`
    sink. The picture Лілі chose to send rides the same single-writer outbox; the v0.13 outbound daemon
    sends the record's ``photo`` as a Telegram photo with ``caption`` as the caption.
    """
    return fifo.append(outbox_path, caption, kind="lili", photo=str(abs_path))


def make_photo_sink(outbox_path: str | Path) -> Callable[[str, str], None]:
    """Build the ``telegram_sink`` the core's `send_image` tool calls (v0.24).

    A thin closure over :func:`mirror_photo` — keeping the TUI the **single outbox writer** (the core
    never touches the outbox). Supplied to ``build_core`` only when the Telegram bridge **and** the image
    tool are on; otherwise the core's sink stays ``None`` (the tool reports the bridge isn't connected).
    """
    def sink(abs_path: str, caption: str) -> None:
        mirror_photo(outbox_path, abs_path, caption)

    return sink

"""TUI ↔ peripheral bridge helpers (v0.13) — drain the `inbox` FIFO, mirror Лілі's replies to `outbox`.

**No Textual dependency** — pure functions over the file bus (`state.fifo`), so they're unit-testable
without the app. The TUI is the inbox **consumer** (reads new lines on idle → runs each as a turn) and
the outbox **producer** (appends **only Лілі's own replies** — never your input → the bridge is
**echo-free by construction**). The Telegram daemons (v0.13) and later voice/dictation share this bus.
"""

from __future__ import annotations

from pathlib import Path

from core.emotion import EmotionState
from state import fifo


def drain_inbox(inbox_path: str | Path, pos_path: str | Path) -> list[str]:
    """The unread `inbox` lines (oldest first); advances the pointer past them. Empty when caught up."""
    records = fifo.read_since(inbox_path, fifo.load_pointer(pos_path))
    if records:
        fifo.save_pointer(pos_path, records[-1]["id"])
    return [r["text"] for r in records]


def mirror_reply(outbox_path: str | Path, state: EmotionState) -> int:
    """Append Лілі's reply (``kind="lili"`` + her emotion) to the `outbox`; return the new id.

    Called once per turn at the point her reply is shown — keyboard, Telegram, or a spoken proactive
    thought alike.
    """
    return fifo.append(
        outbox_path, state.reply, kind="lili",
        emotion=state.emotion.value, intensity=state.intensity,
    )


def mirror_user(outbox_path: str | Path, text: str) -> int:
    """Append **your keyboard message** (``kind="user"``) to the `outbox` so it shows in Telegram too.

    Only for **keyboard** turns — a Telegram-originated message is *not* passed here (it's already in
    Telegram; re-sending it would echo). This is the symmetric half of the `📱` inbox line in the TUI.
    """
    return fifo.append(outbox_path, text, kind="user")

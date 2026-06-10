"""Daemon 1 — ``telegram → inbox`` (v0.13, LUMI-055).

Receive Telegram messages, consolidate a burst in an **in-memory** buffer, and flush every
``LUMI_TELEGRAM_FLUSH_S`` seconds into **one** ``inbox`` record. The Telegram offset is advanced
(acked) **only after** a successful flush — so a crash before a flush makes Telegram re-deliver
(no buffer file needed). Only **allowlisted** senders are buffered.

``Inbound`` is pure (buffer / flush-then-ack / allowlist / dedup) and unit-tested. ``run()`` is the
``aiogram`` long-poll glue (lazy import; not covered).
"""

from __future__ import annotations

from pathlib import Path

from state import fifo


class Inbound:
    """The testable heart of daemon 1 — see the module docstring.

    ``pending_offset`` is the highest update id received; ``acked_offset`` is the highest one
    **durably flushed**. The long-poll asks Telegram for updates after ``acked_offset`` — so any
    update received but not yet flushed is re-delivered on a crash (``receive`` dedups it).
    """

    def __init__(self, inbox_path: str | Path, allowlist: set[int]) -> None:
        self._inbox = inbox_path
        self._allow = set(allowlist)
        self._buffer: list[str] = []
        self.pending_offset = 0  # highest update id received (acked or not)
        self.acked_offset = 0  # highest update id durably flushed

    def receive(self, update_id: int, user_id: int, text: str) -> None:
        """Buffer one message's text iff the sender is allowlisted; track the offset.

        Updates at or below ``pending_offset`` are **ignored** (a re-delivery of an already-seen
        update while we hadn't acked yet) — so a crash/replay never double-buffers.
        """
        if update_id <= self.pending_offset:
            return  # dedup the re-delivery
        self.pending_offset = update_id
        if user_id in self._allow:
            self._buffer.append(text)

    def flush(self) -> int | None:
        """Consolidate the buffer into one ``inbox`` record, **then** ack. Return the id (or None).

        The ack (``acked_offset = pending_offset``) happens only **after** the append — if the
        append raises, the offset is not advanced, so Telegram re-delivers (ack-after-flush).
        Non-allowlisted updates carry no buffered text but are still acked (we don't want them
        re-delivered forever).
        """
        rec_id: int | None = None
        if self._buffer:
            rec_id = fifo.append(self._inbox, "\n".join(self._buffer), source="telegram")
            self._buffer.clear()
        self.acked_offset = self.pending_offset
        return rec_id

    @property
    def buffered(self) -> int:
        """How many messages are waiting for the next flush (testing/observability)."""
        return len(self._buffer)


def run() -> None:  # pragma: no cover - aiogram long-poll glue (network, no paid CI)
    """Long-poll Telegram and feed an ``Inbound`` (ack-after-flush). Requires ``aiogram`` + a token."""
    import asyncio

    from aiogram import Bot

    from core.config import load_config

    from . import get_logger

    cfg = load_config()
    if not cfg.telegram_token:
        raise SystemExit("LUMI_TELEGRAM_TOKEN is not set")
    if not cfg.telegram_allowlist:
        raise SystemExit("LUMI_TELEGRAM_ALLOWLIST is empty — refusing to serve everyone")

    log = get_logger("inbound", cfg.inbox_path.parent)
    allow = set(cfg.telegram_allowlist)
    inbound = Inbound(cfg.inbox_path, allow)
    bot = Bot(cfg.telegram_token)
    log.info("inbound up: allowlist=%s, flush=%ss, inbox=%s", sorted(allow), cfg.telegram_flush_s, cfg.inbox_path)

    async def _flusher() -> None:
        while True:
            await asyncio.sleep(cfg.telegram_flush_s)
            pending = inbound.buffered
            rec_id = inbound.flush()
            if rec_id is not None:
                log.info("flushed %d message(s) → inbox id=%d", pending, rec_id)

    async def _main() -> None:
        asyncio.create_task(_flusher())
        while True:
            try:
                # only updates after what we've durably flushed → un-flushed ones replay on a crash
                updates = await bot.get_updates(offset=inbound.acked_offset + 1, timeout=30)
            except Exception as exc:  # noqa: BLE001 — a transient error must not kill the daemon
                log.warning("get_updates failed (retrying): %s", exc)
                await asyncio.sleep(3)
                continue
            for u in updates:
                msg = getattr(u, "message", None)
                if msg and msg.text and msg.from_user:
                    if msg.from_user.id not in allow:
                        log.warning("ignored non-allowlisted id=%s", msg.from_user.id)
                    inbound.receive(u.update_id, msg.from_user.id, msg.text)

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        log.info("inbound stopped")
    finally:
        asyncio.run(bot.session.close())


if __name__ == "__main__":  # pragma: no cover
    run()

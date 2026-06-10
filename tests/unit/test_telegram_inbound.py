"""Daemon 1 — telegram → inbox: buffer, 2s flush, ack-after-flush, allowlist (v0.13, LUMI-055)."""

from state import fifo
from telegram.inbound import Inbound

OWNER, STRANGER = 111, 999


def _inbound(tmp_path):
    return Inbound(tmp_path / "inbox.jsonl", {OWNER}), tmp_path / "inbox.jsonl"


def test_burst_consolidates_into_one_record(tmp_path):
    inb, inbox = _inbound(tmp_path)
    inb.receive(1, OWNER, "привіт")
    inb.receive(2, OWNER, "як справи")
    assert inb.buffered == 2
    rec_id = inb.flush()
    records = fifo.read_since(inbox, 0)
    assert len(records) == 1 and records[0]["id"] == rec_id  # one consolidated record
    assert records[0]["text"] == "привіт\nяк справи" and records[0]["source"] == "telegram"
    assert inb.buffered == 0  # buffer cleared


def test_empty_flush_writes_nothing(tmp_path):
    inb, inbox = _inbound(tmp_path)
    assert inb.flush() is None
    assert fifo.read_since(inbox, 0) == []


def test_allowlist_blocks_strangers(tmp_path):
    inb, inbox = _inbound(tmp_path)
    inb.receive(1, STRANGER, "spam")  # not allowlisted → not buffered…
    assert inb.buffered == 0
    inb.flush()
    assert fifo.read_since(inbox, 0) == []  # …never reaches the inbox
    assert inb.acked_offset == 1  # …but is still acked (won't be re-delivered forever)


def test_ack_after_flush(tmp_path):
    inb, _ = _inbound(tmp_path)
    inb.receive(5, OWNER, "hi")
    assert inb.pending_offset == 5 and inb.acked_offset == 0  # received, NOT yet acked
    inb.flush()
    assert inb.acked_offset == 5  # ack only advances after the flush


def test_redelivery_is_deduped(tmp_path):
    # A crash before a flush → Telegram replays update 1; receive must not double-buffer it.
    inb, inbox = _inbound(tmp_path)
    inb.receive(1, OWNER, "hi")
    inb.receive(1, OWNER, "hi")  # replay (same update id, not yet acked)
    assert inb.buffered == 1
    inb.flush()
    assert fifo.read_since(inbox, 0)[0]["text"] == "hi"  # once, not "hi\nhi"

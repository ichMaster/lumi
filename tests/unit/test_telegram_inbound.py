"""Daemon 1 — telegram → inbox: buffer, 2s flush, ack-after-flush, allowlist (v0.13, LUMI-055).

Plus v0.26.x inbound voice: a Telegram voice note → STT → inbox (voice_to_text, mock bot + MockSTT).
"""

import io

from state import fifo
from telegram.inbound import Inbound, voice_to_text
from voice.stt import MockSTT

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


def test_empty_text_acks_but_buffers_nothing(tmp_path):
    # v0.26.x: a failed voice STT yields "" → advance the offset (no re-poll/re-bill) but buffer nothing.
    inb, inbox = _inbound(tmp_path)
    inb.receive(7, OWNER, "")
    inb.receive(8, OWNER, "   ")  # whitespace-only too
    assert inb.buffered == 0 and inb.pending_offset == 8
    inb.flush()
    assert fifo.read_since(inbox, 0) == [] and inb.acked_offset == 8


# --- inbound voice: a Telegram voice note → STT → text (mock bot + MockSTT) ------------------------
class _Voice:
    file_id = "v1"


class _File:
    file_path = "voice/file.ogg"


class _Bot:
    """A fake aiogram bot — get_file + download_file return canned audio (no network)."""

    def __init__(self, audio=b"OGGDATA", boom=False):
        self._audio, self._boom = audio, boom
        self.downloaded: list[str] = []

    async def get_file(self, file_id):
        if self._boom:
            raise RuntimeError("telegram down")
        return _File()

    async def download_file(self, path):
        self.downloaded.append(path)
        return io.BytesIO(self._audio)


async def test_voice_to_text_transcribes(tmp_path):
    bot, stt = _Bot(b"OGGDATA"), MockSTT("привіт з телеграму")
    text = await voice_to_text(bot, _Voice(), stt)
    assert text == "привіт з телеграму"
    assert bot.downloaded == ["voice/file.ogg"]
    assert stt.calls == [(len(b"OGGDATA"), "uk")]  # the downloaded audio reached the STT


async def test_voice_to_text_empty_recognition_drops():
    assert await voice_to_text(_Bot(), _Voice(), MockSTT("")) == ""  # silence → "" (dropped)


async def test_voice_to_text_failure_never_raises():
    assert await voice_to_text(_Bot(boom=True), _Voice(), MockSTT("x")) == ""  # network error → "" not a crash

"""v0.24 LUMI-100 — daemon 2 sends a chosen send_image record as a photo (mocked bot, no aiogram).

`batches` isolates a photo record (own group); `send_photo_record` sends it via send_photo ALWAYS (not
LUMI_TELEGRAM_PHOTO-gated) and on its own, caption-capped, degrading to text when the file is gone.
"""
from __future__ import annotations

from datetime import UTC, datetime

from state import fifo
from telegram.outbound import (
    CAPTION_LIMIT,
    batches,
    is_photo_record,
    send_photo_record,
    voice_to_telegram,
)
from voice.tts import MockTTS

_NOW = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)


def _txt(id_, text, emotion="calm"):
    return {"id": id_, "text": text, "ts": _NOW.isoformat(), "emotion": emotion, "intensity": 0.5}


def _photo(id_, text, path, emotion="joy"):
    return {"id": id_, "text": text, "ts": _NOW.isoformat(), "kind": "lili",
            "emotion": emotion, "intensity": 0.8, "photo": str(path)}


class _Bot:
    """A fake aiogram bot — records send_photo / send_message calls (no network)."""

    def __init__(self) -> None:
        self.photos: list[tuple] = []
        self.messages: list[tuple] = []

    async def send_photo(self, chat, file, caption=None):
        self.photos.append((chat, file, caption))

    async def send_message(self, chat, text):
        self.messages.append((chat, text))


# --- is_photo_record / batches isolation ----------------------------------------------------------
def test_is_photo_record():
    assert is_photo_record(_photo(1, "x", "/a/c.png"))
    assert not is_photo_record(_txt(2, "y"))
    assert not is_photo_record({"id": 3, "text": "u", "kind": "user"})  # a mirrored keyboard line


def test_batches_isolate_a_photo_on_its_own():
    recs = [_txt(1, "a"), _txt(2, "b"), _photo(3, "cap", "/x/c.png"), _txt(4, "c"), _txt(5, "d")]
    groups = batches(recs, 2)
    assert [len(g) for g in groups] == [2, 1, 2]  # [a,b] | [photo] | [c,d] — the photo never merges
    assert groups[1][0]["photo"] == "/x/c.png"


def test_batches_no_photo_unchanged():
    recs = [_txt(i, f"r{i}") for i in range(1, 7)]
    assert [len(g) for g in batches(recs, 2)] == [2, 2, 2]  # plain N-batch unchanged


# --- send_photo_record: always a photo, on its own ------------------------------------------------
async def test_send_photo_record_sends_photo_with_caption(tmp_path):
    img = tmp_path / "cat.png"
    img.write_bytes(b"PNG")
    bot = _Bot()
    await send_photo_record(bot, ["123"], _photo(1, "глянь", img), fs_input=str)
    assert len(bot.photos) == 1 and bot.messages == []  # a single photo, no separate text
    chat, file, caption = bot.photos[0]
    assert chat == "123" and file == str(img) and caption.startswith("глянь ")  # text + emoji as caption


def test_send_photo_record_caption_is_capped(tmp_path):
    # render appends an emoji; caption_for would truncate — send_photo_record uses the FULL text and
    # only captions when it fits, so a ≤cap text rides as the caption.
    img = tmp_path / "c.png"
    img.write_bytes(b"P")
    rec = _photo(1, "short", img)
    from telegram.outbound import render
    assert len(render([rec])) <= CAPTION_LIMIT  # a normal reply fits a caption


async def test_send_photo_record_long_text_splits(tmp_path):
    img = tmp_path / "c.png"
    img.write_bytes(b"P")
    bot = _Bot()
    await send_photo_record(bot, ["c"], _photo(1, "x" * 1100, img), fs_input=str)  # > 1024 caption cap
    assert len(bot.photos) == 1 and bot.photos[0][2] is None  # photo without a caption
    assert bot.messages and all(len(m[1]) <= 4096 for m in bot.messages)  # words as message(s)


async def test_send_photo_record_missing_file_degrades_to_text(tmp_path):
    bot = _Bot()
    await send_photo_record(bot, ["c"], _photo(1, "ось", tmp_path / "gone.png"), fs_input=str)
    assert bot.photos == []  # nothing to send as a photo
    assert bot.messages and "ось" in bot.messages[0][1]  # her words still arrive


async def test_send_photo_record_each_chat(tmp_path):
    img = tmp_path / "c.png"
    img.write_bytes(b"P")
    bot = _Bot()
    await send_photo_record(bot, ["a", "b"], _photo(1, "hi", img), fs_input=str)
    assert [p[0] for p in bot.photos] == ["a", "b"]  # one photo per allowlisted chat


# --- voice mode: a chosen image is a PHOTO, not voiced (0.24.1 fix) --------------------------------
async def test_voice_mode_sends_photo_not_voiced(tmp_path):
    outbox, sent = tmp_path / "outbox.jsonl", tmp_path / "outbox.sent"
    img = tmp_path / "cat.png"
    img.write_bytes(b"PNG")
    fifo.append(outbox, "привіт", kind="lili", emotion="joy")             # a spoken reply → voiced
    fifo.append(outbox, "ось малюнок", kind="lili", emotion="calm", photo=str(img))  # a chosen image → photo
    fifo.append(outbox, "моя репліка", kind="user")                       # your mirrored line → skipped
    voiced, photos = [], []

    async def fake_send_voice(ogg, caption):
        voiced.append(caption)

    async def fake_send_photo(rec):
        photos.append(rec["text"])

    n = await voice_to_telegram(
        outbox, sent, tts=MockTTS(), to_ogg=lambda b: b,
        send_voice=fake_send_voice, caption_for=lambda r: r["text"],
        send_photo=fake_send_photo,
    )
    assert n == 2                          # the reply (voiced) + the image (photo); the user line skipped
    assert voiced == ["привіт"]            # only the spoken reply was voiced (not the image's caption)
    assert photos == ["ось малюнок"]       # the chosen image went out as a PHOTO, not voiced
    assert fifo.load_pointer(sent) == 3    # advanced past all three


async def test_voice_mode_without_send_photo_falls_back_to_voicing(tmp_path):
    """Legacy callers (no send_photo injected) still advance — the photo record is voiced as before."""
    outbox, sent = tmp_path / "outbox.jsonl", tmp_path / "outbox.sent"
    fifo.append(outbox, "ось", kind="lili", emotion="calm", photo="/x/cat.png")
    voiced = []

    async def fake_send_voice(ogg, caption):
        voiced.append(caption)

    n = await voice_to_telegram(
        outbox, sent, tts=MockTTS(), to_ogg=lambda b: b,
        send_voice=fake_send_voice, caption_for=lambda r: r["text"],  # no send_photo
    )
    assert n == 1 and voiced == ["ось"] and fifo.load_pointer(sent) == 1  # back-compat: voiced, advanced

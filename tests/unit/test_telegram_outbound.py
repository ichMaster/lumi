"""Daemon 2 — outbox → telegram: FIFO N-batch, catch-up cap, emoji/photo (v0.13, LUMI-056)."""

from datetime import UTC, datetime, timedelta

from telegram.outbound import (
    CAPTION_LIMIT,
    MESSAGE_LIMIT,
    batches,
    caption_for,
    chunk,
    portrait_for,
    render,
    split_catchup,
)
from voice.tts import MockTTS

_NOW = datetime(2026, 6, 10, 14, 0, tzinfo=UTC)


def _rec(id_, text, ts, emotion="calm", intensity=0.5):
    return {"id": id_, "text": text, "ts": ts.isoformat(), "emotion": emotion, "intensity": intensity}


# --- N-batch (bounds a backlog) -------------------------------------------
def test_batches_bound_a_backlog():
    recs = [_rec(i, f"r{i}", _NOW) for i in range(1, 7)]  # 6 records
    groups = batches(recs, 2)
    assert [len(g) for g in groups] == [2, 2, 2]  # ⌈6/2⌉ = 3 messages, never one blob
    assert batches(recs, 1) == [[r] for r in recs]  # N=1 → one per message
    assert len(batches(recs, 99)) == 1  # N huge → still capped at the record count


# --- catch-up cap ---------------------------------------------------------
def test_split_catchup_skips_stale():
    old = _rec(1, "ancient", _NOW - timedelta(hours=30))  # older than 24h
    older = _rec(2, "also old", _NOW - timedelta(hours=25))
    fresh = _rec(3, "recent", _NOW - timedelta(minutes=5))
    stale, keep = split_catchup([old, older, fresh], _NOW, catchup_h=24)
    assert [r["id"] for r in stale] == [1, 2]  # the old prefix is skipped
    assert [r["id"] for r in keep] == [3]      # only the fresh one is sent


def test_split_catchup_unparseable_ts_is_fresh():
    bad = {"id": 1, "text": "x", "ts": "not-a-date", "emotion": "calm"}
    stale, keep = split_catchup([bad], _NOW, catchup_h=24)
    assert stale == [] and keep == [bad]  # never silently dropped


# --- render (emoji appended) ----------------------------------------------
def test_render_appends_emoji_and_joins():
    out = render([_rec(1, "привіт", _NOW, "joy", 0.8), _rec(2, "хм", _NOW, "thoughtful", 0.5)])
    assert "привіт" in out and "хм" in out
    assert out.split("\n\n")[0] != "привіт"  # an emoji glyph was appended
    assert "\n\n" in out  # two replies, blank-line separated


def test_render_unknown_emotion_falls_back():
    out = render([_rec(1, "hey", _NOW, emotion="nonsense", intensity=0.5)])
    assert out.startswith("hey ")  # renders with the calm-default glyph, no crash


def test_render_user_line_is_prefixed_no_emoji():
    from telegram.outbound import USER_PREFIX
    user = {"id": 1, "text": "як ти?", "ts": _NOW.isoformat(), "kind": "user"}
    lili = _rec(2, "добре!", _NOW, "joy", 0.8)
    out = render([user, lili])  # a mixed batch: your line, then her reply
    assert out.split("\n\n")[0] == f"{USER_PREFIX} як ти?"  # your line: prefixed, no emoji
    assert out.split("\n\n")[1].startswith("добре! ")        # her reply: text + emoji


# --- chunk (length guard — the photo-caption gotcha) ----------------------
def test_chunk_short_passes_through():
    assert chunk("hi", MESSAGE_LIMIT) == ["hi"]


def test_chunk_splits_long_text_under_limit():
    text = "\n".join(f"line {i}" for i in range(2000))  # well over 4096 chars
    pieces = chunk(text, MESSAGE_LIMIT)
    assert len(pieces) > 1
    assert all(len(p) <= MESSAGE_LIMIT for p in pieces)       # every piece fits
    assert "".join(p.replace("\n", "") for p in pieces) == text.replace("\n", "")  # nothing lost


def test_chunk_caption_limit_is_tighter():
    text = "x " * 700  # ~1400 chars — fits a message, NOT a 1024 caption
    assert len(chunk(text, MESSAGE_LIMIT)) == 1
    assert all(len(p) <= CAPTION_LIMIT for p in chunk(text, CAPTION_LIMIT))


# --- voice mode (LUMI_TELEGRAM_VOICE) -------------------------------------
from state import fifo  # noqa: E402


def _fakes(sent_log):
    async def send_text(group):
        sent_log.append(("text", [r["text"] for r in group]))

    async def send_photo(rec):
        sent_log.append(("photo", rec["text"]))

    async def send_voice(ogg, caption):
        sent_log.append(("voice", ogg, caption))

    return {"send_text": send_text, "send_photo": send_photo, "send_voice": send_voice}


async def _forward(tmp_path, sent_log, **kw):
    from telegram.outbound import forward

    args = {"batch": 5, "voice_dir": tmp_path / "voice", "to_ogg": lambda b: b"OGG:" + b,
            "caption_for": lambda r: r["text"], **_fakes(sent_log), **kw}
    return await forward(tmp_path / "outbox.jsonl", tmp_path / "outbox.sent", **args)


async def test_voice_mode_voices_an_old_record_and_skips_your_line(tmp_path):
    """Records written before v4.6 (no ``voice`` key) go the old way in voice mode: her lines voiced by the daemon's
    TTS, your mirrored line never sent."""
    outbox, sent = tmp_path / "outbox.jsonl", tmp_path / "outbox.sent"
    fifo.append(outbox, "привіт", kind="lili", emotion="joy")
    fifo.append(outbox, "моя репліка", kind="user")          # your mirrored keyboard line
    fifo.append(outbox, "ще", kind="lili", emotion="calm")
    tts, sent_log = MockTTS(), []
    assert await _forward(tmp_path, sent_log, tts=tts, voice_mode=True) == (3, False)
    assert [c for _, _, c in sent_log] == ["привіт", "ще"]      # the user line was NOT sent
    assert [t for t, _ in tts.calls] == ["привіт", "ще"]          # nor synthesized
    assert sent_log[0][1] == b"OGG:AUDIO:" + "привіт".encode()   # mp3→ogg ran on the synth
    assert fifo.load_pointer(sent) == 3                          # advanced past all 3 (incl. the user)
    assert await _forward(tmp_path, sent_log, tts=tts, voice_mode=True) == (0, False)  # caught up


async def test_voice_mode_retry_on_failure(tmp_path):
    outbox, sent = tmp_path / "outbox.jsonl", tmp_path / "outbox.sent"
    fifo.append(outbox, "a", kind="lili")

    async def boom(_ogg, _caption):
        raise RuntimeError("network")

    assert await _forward(tmp_path, [], tts=MockTTS(), voice_mode=True, send_voice=boom) == (0, True)
    assert fifo.load_pointer(sent) == 0                     # NOT advanced → will retry


# --- v4.6: the server's stored voice file ----------------------------------------------------------------
VID = "0123456789abcdef0123456789abcdef"


def _stored(tmp_path, vid=VID, data=b"MP3"):
    (tmp_path / "voice").mkdir(exist_ok=True)
    (tmp_path / "voice" / f"{vid}.mp3").write_bytes(data)


async def test_a_record_with_a_stored_file_goes_as_that_voice_and_never_calls_tts(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    _stored(tmp_path)
    fifo.append(outbox, "привіт", kind="lili", emotion="joy", speech="[laughs] привіт", voice=f"{VID}.mp3")
    tts, sent_log = MockTTS(), []
    assert await _forward(tmp_path, sent_log, tts=tts, voice_mode=True) == (1, False)
    assert sent_log == [("voice", b"OGG:MP3", "привіт")]  # the caption: her words, never the tags
    assert tts.calls == []  # one ElevenLabs call per line — the server's


async def test_a_record_with_voice_null_goes_as_text_batched(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    for n in range(3):
        fifo.append(outbox, f"r{n}", kind="lili", emotion="calm", voice=None)
    tts, sent_log = MockTTS(), []
    assert await _forward(tmp_path, sent_log, tts=tts, voice_mode=True, batch=2) == (3, False)
    assert sent_log == [("text", ["r0", "r1"]), ("text", ["r2"])] and tts.calls == []


async def test_outside_voice_mode_an_old_record_goes_as_text_and_your_line_too(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    fifo.append(outbox, "моя репліка", kind="user")
    fifo.append(outbox, "стара", kind="lili", emotion="calm")
    tts, sent_log = MockTTS(), []
    assert await _forward(tmp_path, sent_log, tts=tts, voice_mode=False) == (2, False)
    assert sent_log == [("text", ["моя репліка", "стара"])] and tts.calls == []
    fifo.append(outbox, "ще стара", kind="lili", emotion="calm")
    assert await _forward(tmp_path, sent_log, voice_mode=True) == (1, False)  # voice mode without a TTS: text
    assert sent_log[-1] == ("text", ["ще стара"])


async def test_a_missing_or_badly_named_file_degrades_to_text(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    (tmp_path / "voice").mkdir()
    (tmp_path / "secret.mp3").write_bytes(b"SECRET")
    (tmp_path / "voice" / f"{'b' * 32}.mp3").symlink_to(tmp_path / "secret.mp3")
    fifo.append(outbox, "зникла", kind="lili", voice=f"{'a' * 32}.mp3")       # expired / missing
    fifo.append(outbox, "посилання", kind="lili", voice=f"{'b' * 32}.mp3")    # a link — never read
    fifo.append(outbox, "погана назва", kind="lili", voice="../secret.mp3")   # never read
    sent_log = []
    assert await _forward(tmp_path, sent_log, tts=MockTTS(), voice_mode=True) == (3, False)
    assert sent_log == [("text", ["зникла"]), ("text", ["посилання"]), ("text", ["погана назва"])]
    assert all(b"SECRET" not in str(x).encode() for x in sent_log)


async def test_a_file_that_wont_convert_goes_as_text(tmp_path):
    _stored(tmp_path)
    fifo.append(tmp_path / "outbox.jsonl", "слова", kind="lili", voice=f"{VID}.mp3")

    def broken(_mp3):
        raise RuntimeError("ffmpeg failed")

    sent_log = []
    assert await _forward(tmp_path, sent_log, to_ogg=broken) == (1, False)
    assert sent_log == [("text", ["слова"])]


async def test_the_order_holds_voices_stand_alone_and_text_batches_around_them(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    _stored(tmp_path)
    img = tmp_path / "cat.png"
    img.write_bytes(b"PNG")
    fifo.append(outbox, "t1", kind="lili", voice=None)
    fifo.append(outbox, "t2", kind="user")
    fifo.append(outbox, "голос", kind="lili", voice=f"{VID}.mp3")
    fifo.append(outbox, "t3", kind="lili", voice=None)
    fifo.append(outbox, "фото", kind="lili", photo=str(img))
    fifo.append(outbox, "стара", kind="lili")
    sent_log, tts = [], MockTTS()
    assert await _forward(tmp_path, sent_log, tts=tts) == (6, False)
    assert sent_log == [("text", ["t1", "t2"]), ("voice", b"OGG:MP3", "голос"), ("text", ["t3"]),
                        ("photo", "фото"), ("text", ["стара"])]


async def test_a_failed_voice_send_leaves_the_pointer_before_it(tmp_path):
    outbox, sent = tmp_path / "outbox.jsonl", tmp_path / "outbox.sent"
    _stored(tmp_path)
    fifo.append(outbox, "текст", kind="lili", voice=None)
    fifo.append(outbox, "голос", kind="lili", voice=f"{VID}.mp3")
    fifo.append(outbox, "після", kind="lili", voice=None)

    async def boom(_ogg, _caption):
        raise RuntimeError("network")

    sent_log = []
    assert await _forward(tmp_path, sent_log, send_voice=boom) == (1, True)
    assert fifo.load_pointer(sent) == 1 and sent_log == [("text", ["текст"])]  # "після" waits — never skipped
    assert await _forward(tmp_path, sent_log) == (2, False)
    assert [x[0] for x in sent_log] == ["text", "voice", "text"]


def test_the_delivery_of_each_record():
    from telegram.outbound import delivery

    assert delivery({"kind": "lili", "voice": f"{VID}.mp3"}) == "stored"
    assert delivery({"kind": "lili", "voice": None}) == "text"
    assert delivery({"kind": "lili", "voice": "x.mp3"}) == "text"
    assert delivery({"kind": "lili"}) == "text" and delivery({"kind": "lili"}, voice_mode=True) == "old"
    assert delivery({"kind": "user"}) == "text" and delivery({"kind": "user"}, voice_mode=True) == "skip"
    assert delivery({"kind": "lili", "photo": "/p.png", "voice": None}, voice_mode=True) == "photo"


def test_voice_caption_carries_emoji_and_is_capped():
    rec = {"id": 1, "text": "привіт", "emotion": "joy", "intensity": 0.8}
    cap = caption_for(rec)
    assert cap.startswith("привіт ") and len(cap) > len("привіт")  # text + emoji
    long = {"id": 2, "text": "x" * 2000, "emotion": "calm", "intensity": 0.5}
    assert len(caption_for(long)) <= CAPTION_LIMIT          # truncated to the caption cap


# --- portrait (graceful) --------------------------------------------------
def test_portrait_for(tmp_path):
    (tmp_path / "calm.png").write_bytes(b"x")
    assert portrait_for(tmp_path, "calm") == tmp_path / "calm.png"  # present
    assert portrait_for(tmp_path / "empty", "joy") is None          # absent → text-only

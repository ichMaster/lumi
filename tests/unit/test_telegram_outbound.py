"""Daemon 2 — outbox → telegram: FIFO N-batch, catch-up cap, emoji/photo (v0.13, LUMI-056)."""

from datetime import UTC, datetime, timedelta

from telegram.outbound import (
    CAPTION_LIMIT,
    MESSAGE_LIMIT,
    batches,
    chunk,
    portrait_for,
    render,
    split_catchup,
)

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


# --- portrait (graceful) --------------------------------------------------
def test_portrait_for(tmp_path):
    (tmp_path / "calm.png").write_bytes(b"x")
    assert portrait_for(tmp_path, "calm") == tmp_path / "calm.png"  # present
    assert portrait_for(tmp_path / "empty", "joy") is None          # absent → text-only

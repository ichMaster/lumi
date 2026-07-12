"""Contract tests for the per-user memory record shapes (v0.2).

Pins ARCHITECTURE §Data model / §Contracts. Changing a record shape must change
this test.
"""

from core.repository import (
    Closeness,
    DaySummary,
    LongTermFact,
    Message,
    SessionDigest,
    ShortSummary,
    VectorRecord,
    WeekSummary,
)


def test_message_shape():
    # v0.2 base + the v0.3 emotion field + the v1.1 `move` (additive, None-defaulted so
    # pre-v1.1 stores load without migration — the v0.2 shim pattern).
    assert set(Message.__dataclass_fields__) == {
        "session_id", "user_id", "role", "text", "ts", "emotion", "intensity", "move",
    }


def test_message_move_defaults_to_none():
    # Additive proof: a record built without `move` (any pre-v1.1 caller/row) carries None.
    m = Message(
        session_id="s1", user_id="owner", role="lili", text="hi",
        ts="2026-01-01T00:00:00+00:00",
    )
    assert m.move is None


def test_day_summary_shape():
    # v0.9.x: a local day consolidated into ≤4 rows, per-user; `count` drives staleness.
    assert set(DaySummary.__dataclass_fields__) == {"user_id", "date", "summary", "count", "ts"}


def test_week_summary_shape():
    # date-based recall: a Mon–Sun week consolidated, per-user; keyed by the Monday `week_start`.
    assert set(WeekSummary.__dataclass_fields__) == {
        "user_id", "week_start", "summary", "count", "ts",
    }


def test_closeness_shape():
    # v0.10: per-user relationship level — value + 1–5 bucket + last interaction ts.
    assert set(Closeness.__dataclass_fields__) == {"user_id", "value", "level", "last_ts"}


def test_closeness_is_per_user():
    c = Closeness(user_id="owner", value=50.0, level=3, last_ts="2026-06-09T00:00:00+00:00")
    assert c.user_id == "owner" and c.level == 3


def test_short_summary_shape():
    # v0.9: two tiers — detailed `summary` + one-line `gist`.
    fields = set(ShortSummary.__dataclass_fields__)
    assert fields == {"user_id", "session_id", "summary", "gist", "ts"}


def test_short_summary_is_per_user():
    # Every short-memory record carries the owning user_id (no cross-user leak).
    s = ShortSummary(
        user_id="owner", session_id="s1", summary="детальний", gist="стисло",
        ts="2026-06-06T00:00:00+00:00",
    )
    assert s.user_id == "owner" and s.gist == "стисло"


def test_long_term_fact_shape():
    # v0.36: core (identity-core) + obsolete (hygiene) are additive — old records default False.
    fields = set(LongTermFact.__dataclass_fields__)
    assert fields == {"user_id", "fact", "meta", "confidence", "ts", "core", "obsolete"}


def test_long_term_fact_flags_default_false():
    f = LongTermFact(user_id="owner", fact="loves tea", meta="", confidence=0.5,
                     ts="2026-06-06T00:00:00+00:00")
    assert f.core is False and f.obsolete is False


def test_long_term_fact_is_per_user():
    f = LongTermFact(user_id="owner", fact="loves tea", meta="", confidence=0.5,
                     ts="2026-06-06T00:00:00+00:00")
    assert f.user_id == "owner"


def test_session_digest_shape():
    # In-session compaction record (per-session, behind repository).
    fields = set(SessionDigest.__dataclass_fields__)
    assert fields == {"session_id", "summary", "compacted_count", "ts"}


def test_vector_record_shape():
    # v0.16 recall + v0.30 chunking + v0.36 fact embedding — ARCHITECTURE §Semantic recall:
    # {user_id, msg_id, vector, text, ts, role, parent_msg_id, chunk_index, kind}.
    assert set(VectorRecord.__dataclass_fields__) == {
        "user_id", "msg_id", "vector", "text", "ts", "role", "parent_msg_id", "chunk_index", "kind",
    }


def test_vector_record_kind_defaults_to_message():
    # v0.36: kind is additive — old records (no kind) load as the message layer (back-compatible).
    r = VectorRecord(user_id="owner", msg_id="abc", vector=[0.1, 0.2],
                     text="привіт", ts="2026-06-06T00:00:00+00:00", role="user")
    assert r.kind == "message"


def test_vector_record_is_per_user_and_coerces_vector():
    # Per-user (carries user_id); JSON round-trips the vector list back into a tuple.
    r = VectorRecord(user_id="owner", msg_id="abc", vector=[0.1, 0.2],
                     text="привіт", ts="2026-06-06T00:00:00+00:00", role="user")
    assert r.user_id == "owner"
    assert r.vector == (0.1, 0.2)  # list coerced to tuple in __post_init__
    # v0.30 back-compat: a record without parent_msg_id is its own parent (the one-chunk / v0.16 case).
    assert r.parent_msg_id == "abc" and r.chunk_index == 0


def test_vector_record_chunk_fields():
    # v0.30: a chunk carries its parent message id + 0-based ordinal within that message.
    r = VectorRecord(user_id="owner", msg_id="chunk1", vector=[0.0], text="passage",
                     ts="2026-06-06T00:00:00+00:00", role="user", parent_msg_id="msgA", chunk_index=2)
    assert r.parent_msg_id == "msgA" and r.chunk_index == 2

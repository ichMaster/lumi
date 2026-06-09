"""Contract tests for the per-user memory record shapes (v0.2).

Pins ARCHITECTURE §Data model / §Contracts. Changing a record shape must change
this test.
"""

from core.repository import DaySummary, LongTermFact, SessionDigest, ShortSummary


def test_day_summary_shape():
    # v0.9.x: a local day consolidated into ≤4 rows, per-user.
    assert set(DaySummary.__dataclass_fields__) == {"user_id", "date", "summary", "ts"}


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
    fields = set(LongTermFact.__dataclass_fields__)
    assert fields == {"user_id", "fact", "meta", "confidence", "ts"}


def test_long_term_fact_is_per_user():
    f = LongTermFact(user_id="owner", fact="loves tea", meta="", confidence=0.5,
                     ts="2026-06-06T00:00:00+00:00")
    assert f.user_id == "owner"


def test_session_digest_shape():
    # In-session compaction record (per-session, behind repository).
    fields = set(SessionDigest.__dataclass_fields__)
    assert fields == {"session_id", "summary", "compacted_count", "ts"}

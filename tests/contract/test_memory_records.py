"""Contract tests for the per-user memory record shapes (v0.2).

Pins ARCHITECTURE §Data model / §Contracts. Changing a record shape must change
this test.
"""

from core.repository import ShortSummary


def test_short_summary_shape():
    fields = set(ShortSummary.__dataclass_fields__)
    assert fields == {"user_id", "session_id", "summary", "ts"}


def test_short_summary_is_per_user():
    # Every short-memory record carries the owning user_id (no cross-user leak).
    s = ShortSummary(user_id="owner", session_id="s1", summary="gist", ts="2026-06-06T00:00:00+00:00")
    assert s.user_id == "owner"

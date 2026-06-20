"""`/recall` filtering — `exclude_session` drops the active conversation's own hits, and
`before`/`after` scope the meaning search to a date range. MockEmbedder, no paid calls.
(core.recall / recall_moments)"""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.embedder import MockEmbedder
from core.llm import MockLLMClient
from state.local_store import JsonRepository


def _core(tmp_path):
    return Core(
        llm=MockLLMClient("ок"),
        repository=JsonRepository(tmp_path / "store.json"),
        canon="C", model="m", user_id="owner",
        embedder=MockEmbedder(), recall_enabled=True, embed_model="m@x",
        rag_enabled=True, rag_k=5, rag_floor=0.0, rag_max_chars=8000, rag_snippet_chars=4000,
        rag_chunk=True, rag_chunk_chars=40, rag_chunk_overlap=8, rag_chunk_threshold=30, rag_chunk_w=1,
    )


def _index_two_sessions(core):
    """An older session (the 'source') and a current one (the 'echo'), same text → identical
    score, so only the session filter separates them. Returns the current session id."""
    old = core.start_session()
    core.reply("маяк у тумані", old)               # the older source
    cur = core.start_session()
    core.reply("маяк у тумані", cur)               # the current conversation's echo
    core.ensure_backfill()
    return old.id, cur.id


def test_no_filter_returns_both_sessions(tmp_path):
    core = _core(tmp_path)
    old_id, cur_id = _index_two_sessions(core)
    sessions = {r.parent_msg_id for _, r in core.recall("маяк у тумані", k=10)}
    assert sessions                                # unfiltered → hits from both (no exclusion)
    assert core.recall("маяк у тумані", k=10, exclude_session=cur_id)  # still finds the older one


def test_exclude_current_session_drops_its_echoes(tmp_path):
    core = _core(tmp_path)
    old_id, cur_id = _index_two_sessions(core)
    own = core._session_vector_ids(cur_id)
    hits = core.recall("маяк у тумані", k=10, exclude_session=cur_id)
    assert hits                                                  # the older source still surfaces
    assert all(r.parent_msg_id not in own for _, r in hits)     # no hit from the current session
    old_own = core._session_vector_ids(old_id)
    assert any(r.parent_msg_id in old_own for _, r in hits)     # at least one from the older one


def test_exclude_session_is_isolated_to_that_session(tmp_path):
    core = _core(tmp_path)
    old_id, cur_id = _index_two_sessions(core)
    # excluding the OLD session leaves only the current echo
    own_old = core._session_vector_ids(old_id)
    hits = core.recall("маяк у тумані", k=10, exclude_session=old_id)
    assert hits and all(r.parent_msg_id not in own_old for _, r in hits)


def test_recall_moments_threads_the_exclusion(tmp_path):
    core = _core(tmp_path)
    old_id, cur_id = _index_two_sessions(core)
    moments = core.recall_moments("маяк у тумані", exclude_session=cur_id)
    assert moments  # renders from the older session, current-session anchor excluded


# --- date-range filter (before / after) — meaning search scoped to a date window ---------------------
class _Clock:
    """A mutable injected clock so messages land on chosen dates."""
    def __init__(self, dt: datetime) -> None:
        self.dt = dt

    def __call__(self) -> datetime:
        return self.dt


def _date_core(tmp_path, clock):
    return Core(
        llm=MockLLMClient("ок"),
        repository=JsonRepository(tmp_path / "dates.json"),
        canon="C", model="m", user_id="owner", clock=clock,
        embedder=MockEmbedder(), recall_enabled=True, embed_model="m@x",
        rag_enabled=True, rag_k=5, rag_floor=0.0, rag_max_chars=8000, rag_snippet_chars=4000,
    )


def _index_two_dates(core, clock):
    clock.dt = datetime(2026, 6, 11, 10, 0, tzinfo=UTC)
    core.reply("маяк у тумані", core.start_session())     # the older one
    clock.dt = datetime(2026, 6, 19, 10, 0, tzinfo=UTC)
    core.reply("маяк у тумані", core.start_session())     # the newer one (same text)
    core.ensure_backfill()


def test_recall_before_scopes_to_earlier_dates(tmp_path):
    clock = _Clock(datetime(2026, 6, 11, 10, 0, tzinfo=UTC))
    core = _date_core(tmp_path, clock)
    _index_two_dates(core, clock)
    hits = core.recall("маяк у тумані", k=10, before="2026-06-19")
    assert hits and all(r.ts[:10] < "2026-06-19" for _, r in hits)   # only earlier than the bound


def test_recall_after_scopes_to_later_dates(tmp_path):
    clock = _Clock(datetime(2026, 6, 11, 10, 0, tzinfo=UTC))
    core = _date_core(tmp_path, clock)
    _index_two_dates(core, clock)
    hits = core.recall("маяк у тумані", k=10, after="2026-06-19")
    assert hits and all(r.ts[:10] >= "2026-06-19" for _, r in hits)  # only on/after the bound


def test_recall_date_range_window(tmp_path):
    clock = _Clock(datetime(2026, 6, 11, 10, 0, tzinfo=UTC))
    core = _date_core(tmp_path, clock)
    _index_two_dates(core, clock)
    hits = core.recall("маяк у тумані", k=10, after="2026-06-11", before="2026-06-12")
    assert hits and all(r.ts[:10] == "2026-06-11" for _, r in hits)  # the half-open [after, before) window


def test_recall_moments_threads_the_date_filter(tmp_path):
    clock = _Clock(datetime(2026, 6, 11, 10, 0, tzinfo=UTC))
    core = _date_core(tmp_path, clock)
    _index_two_dates(core, clock)
    joined = "\n".join(core.recall_moments("маяк у тумані", before="2026-06-19"))
    assert "2026-06-11" in joined and "2026-06-19" not in joined


def test_recall_moment_shows_message_time(tmp_path):
    # the message ts (HH:MM) is rendered into the recall/RAG moment, not just the date
    clock = _Clock(datetime(2026, 6, 11, 14, 30, tzinfo=UTC))
    core = _date_core(tmp_path, clock)
    core.reply("маяк у тумані", core.start_session())
    core.ensure_backfill()
    joined = "\n".join(core.recall_moments("маяк у тумані"))
    assert "2026-06-11" in joined and "14:30" in joined   # date header + the message time


def test_recall_moment_carries_chainable_msg_id(tmp_path):
    # the /recall + recall-tool moments tag each anchor with a #id (for message_context); ts is shown too
    import re
    clock = _Clock(datetime(2026, 6, 11, 14, 30, tzinfo=UTC))
    core = _date_core(tmp_path, clock)
    core.reply("маяк у тумані", core.start_session())
    core.ensure_backfill()
    joined = "\n".join(core.recall_moments("маяк у тумані"))
    assert re.search(r"#[0-9a-f]{8}\b", joined)   # an 8-hex anchor id is present
    assert "14:30" in joined                      # and the message time (ts)

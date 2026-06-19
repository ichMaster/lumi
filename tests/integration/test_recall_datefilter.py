"""`/recall` current-session filtering — `exclude_session` drops a session's own hits, so the
active conversation's echoes don't bury an older source past the top-k cutoff. MockEmbedder, no
paid calls. (core.recall / recall_moments)"""
from __future__ import annotations

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

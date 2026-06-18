"""v0.30 LUMI-118 — two-level expansion: a long message recalls its relevant PASSAGE, not the whole
message; a short message (and the off-path) recall whole. All via the deterministic MockEmbedder."""
from __future__ import annotations

from core.agent import Core
from core.chunking import chunk_text
from core.embedder import MockEmbedder
from core.llm import MockLLMClient
from state.local_store import JsonRepository


def _core(tmp_path, *, on=True, chunk_w=1, repo=None):
    return Core(
        llm=MockLLMClient("ок"),
        repository=repo or JsonRepository(tmp_path / "store.json"),
        canon="C", model="m", user_id="owner",
        embedder=MockEmbedder(), recall_enabled=True, embed_model="m@x",
        rag_enabled=True, rag_k=5, rag_floor=0.0, rag_max_chars=8000, rag_snippet_chars=4000,
        rag_chunk=on, rag_chunk_chars=40, rag_chunk_overlap=8, rag_chunk_threshold=30, rag_chunk_w=chunk_w,
    )


# distinct tokens per region, so a query lands on one chunk (no shared words across regions)
_LONG = " ".join(f"тема{i} слово{i} деталь{i} нотатка{i}" for i in range(10))


def _index_long(core):
    core.reply(_LONG, core.start_session())
    core.ensure_backfill()


def test_long_message_recalls_passage_not_whole(tmp_path):
    core = _core(tmp_path, on=True, chunk_w=1)
    _index_long(core)
    out = "\n".join(core.recall_moments("слово5", k=1))  # top-1 chunk → its passage
    assert "слово5" in out               # the matched passage is present
    assert "слово0" not in out           # distant regions are excluded (not the whole message)
    assert "слово9" not in out
    assert len(out) < len(_LONG)
    assert "← (matched" in out           # the anchor is marked


def test_off_recalls_the_whole_message(tmp_path):
    core = _core(tmp_path, on=False)
    _index_long(core)
    out = "\n".join(core.recall_moments("слово5", k=1))
    assert "слово0" in out and "слово9" in out  # off → one vector per message → whole message recalled


def test_short_message_recalls_whole_when_chunk_on(tmp_path):
    core = _core(tmp_path, on=True)
    core.reply("коротка згадка про каву сьогодні", core.start_session())
    core.ensure_backfill()
    out = "\n".join(core.recall_moments("каву", k=1))
    assert "коротка згадка про каву сьогодні" in out  # one chunk → the whole message


def test_passage_text_short_returns_whole(tmp_path):
    core = _core(tmp_path, on=True)
    assert core._passage_text("короткий текст", {0}) == "короткий текст"


def test_passage_text_windows_around_matched_chunk(tmp_path):
    core = _core(tmp_path, on=True, chunk_w=1)
    chunks = chunk_text(_LONG, chunk_chars=40, overlap=8, threshold=30)
    assert len(chunks) >= 5
    mid = len(chunks) // 2
    passage = core._passage_text(_LONG, {mid})
    assert chunks[mid][8:20] in passage           # the matched chunk's content is in the passage
    assert "…" in passage                          # trimmed ends are elided
    assert len(passage) < len(_LONG)               # never the whole message

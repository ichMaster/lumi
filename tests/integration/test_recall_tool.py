"""v0.31 LUMI-120/121 — the model-callable `recall` tool on the v0.19 loop: returns top-`k` past
moments (framed as her own **trusted** recollection), a per-turn call cap, dedup against the live
window, and absent when off / without recall+embedder. MockEmbedder — no paid calls."""
from __future__ import annotations

from core.agent import Core
from core.embedder import MockEmbedder
from core.llm import MockLLMClient, is_trusted_text
from core.repository import vector_msg_id
from state.local_store import JsonRepository


def _core(tmp_path, *, recall_tool=True, recall=True, embedder="mock", max_calls=3, store="store.json"):
    emb = MockEmbedder() if embedder == "mock" else embedder
    return Core(
        llm=MockLLMClient("ок"),
        repository=JsonRepository(tmp_path / store),
        canon="C", model="m", user_id="owner",
        embedder=emb, recall_enabled=recall, embed_model="m@x",
        recall_tool_enabled=recall_tool, recall_tool_k=5, recall_tool_max_calls=max_calls,
        rag_enabled=True, rag_k=5, rag_floor=0.0, rag_max_chars=8000, rag_snippet_chars=4000,
    )


def _index(core, text="я люблю каву вранці"):
    core.reply(text, core.start_session())
    core.ensure_backfill()


def test_recall_tool_returns_trusted_moments(tmp_path):
    core = _core(tmp_path)
    _index(core)
    tools, execute = core._recall_tool_args()
    assert tools and tools[0]["name"] == "recall"
    out = execute("recall", {"query": "кава"})
    assert is_trusted_text(out)               # LUMI-121: her own recollection (trusted), not a bare string
    assert "каву" in out["text"]              # the matched moment is returned


def test_recall_tool_forwards_k(tmp_path):
    core = _core(tmp_path)
    _index(core)
    _, execute = core._recall_tool_args()
    assert "каву" in execute("recall", {"query": "кава", "k": 1})["text"]   # k forwarded, still answers


def test_recall_tool_dedups_against_window(tmp_path):
    core = _core(tmp_path)
    s = core.start_session()
    core.reply("я люблю каву вранці", s)
    core.ensure_backfill()
    # everything matching is already in the live window → deduped out → a no-hit notice (plain string)
    msgs = core._repo.load_messages(s.id)
    core._turn_dedup_ids = {vector_msg_id(m.session_id, m.ts, m.role, m.text) for m in msgs}
    _, execute = core._recall_tool_args()
    out = execute("recall", {"query": "кава"})
    assert not is_trusted_text(out) and "згадалося" in out   # nothing new to recall past the window


def test_recall_tool_per_turn_cap(tmp_path):
    core = _core(tmp_path, max_calls=2)
    _index(core)
    _, execute = core._recall_tool_args()
    execute("recall", {"query": "кава"})              # 1
    execute("recall", {"query": "кава"})              # 2
    out = execute("recall", {"query": "кава"})        # 3 → over the cap
    assert "limit reached" in out


def test_recall_tool_empty_query(tmp_path):
    core = _core(tmp_path)
    _index(core)
    _, execute = core._recall_tool_args()
    assert "порожній" in execute("recall", {"query": "   "})


def test_recall_tool_no_hit_notice(tmp_path):
    core = _core(tmp_path)                                # nothing indexed → empty store
    _, execute = core._recall_tool_args()
    out = execute("recall", {"query": "будь-що"})
    assert "згадалося" in out                            # a no-hit notice, never raises


def test_recall_tool_registered_in_turn_tools_when_on(tmp_path):
    core = _core(tmp_path)
    _index(core)
    tools, dispatch = core._turn_tools()
    assert tools is not None and "recall" in {t["name"] for t in tools}
    assert "каву" in dispatch("recall", {"query": "кава"})["text"]   # routed through the dispatch


def test_recall_tool_absent_when_off(tmp_path):
    core = _core(tmp_path, recall_tool=False)
    assert core._recall_tool_args() == (None, None)
    tools, _ = core._turn_tools()                    # no other tools on → (None, None)
    assert "recall" not in {t["name"] for t in (tools or [])}


def test_recall_tool_needs_recall_and_embedder(tmp_path):
    # requested but recall off → absent
    assert _core(tmp_path, recall=False)._recall_tool_args() == (None, None)
    # requested but no embedder → absent (recall_enabled folds in `embedder is not None`)
    no_emb = _core(tmp_path, embedder=None, store="b.json")
    assert no_emb._recall_tool_args() == (None, None)

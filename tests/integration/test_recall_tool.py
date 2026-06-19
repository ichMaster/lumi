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


# --- LUMI-122: behaviour in a turn + graceful degradation (model-driven via tool_script) ------------
class _BoomEmbedder:
    """An embedder that always raises — to prove the recall tool degrades, never crashes a turn."""
    def embed(self, texts, *, is_query=False):
        raise RuntimeError("boom")


def _turn_core(repo, mock, *, embedder):
    # a realistic rag_floor (0.3) so the auto-RAG doesn't pre-surface unrelated messages — that lets the
    # recall tool's *targeted* query find something the auto-RAG "push" didn't already inject.
    return Core(
        llm=mock, repository=repo, canon="C", model="m", user_id="owner",
        embedder=embedder, recall_enabled=True, recall_tool_enabled=True, embed_model="m@x",
        rag_enabled=True, rag_k=5, rag_floor=0.3, rag_max_chars=8000, rag_snippet_chars=4000,
    )


def test_turn_issues_targeted_recall_and_uses_it(tmp_path):
    repo = JsonRepository(tmp_path / "turn.json")
    # seed a PRIOR session with a fact (plain core, no tool script)
    seed = Core(llm=MockLLMClient("ок"), repository=repo, canon="C", model="m", user_id="owner",
                embedder=MockEmbedder(), recall_enabled=True, embed_model="m@x")
    seed.reply("брат живе у Львові", seed.start_session())
    seed.ensure_backfill()
    # a turn whose model is scripted to call recall("брат") — a query ≠ the current message
    mock = MockLLMClient(states={"reply": "ок", "emotion": "calm", "intensity": 0.5},
                         tool_script=[("recall", {"query": "брат"})])
    core = _turn_core(repo, mock, embedder=MockEmbedder())
    state = core.reply("привіт", core.start_session())              # the message is "привіт", not "брат"
    assert ("recall", {"query": "брат"}) in [(c[0], c[1]) for c in mock.tool_calls]   # the targeted query
    result = mock.tool_calls[0][2]                                  # the tool result fed back into the turn
    assert is_trusted_text(result) and "Львові" in result["text"]   # her own memory, used mid-turn
    assert state.emotion == "calm"                                 # {reply, emotion, intensity} contract holds


def test_recall_tool_degrades_on_embedder_error(tmp_path):
    mock = MockLLMClient(states={"reply": "ок", "emotion": "calm", "intensity": 0.5},
                         tool_script=[("recall", {"query": "будь-що"})])
    core = _turn_core(JsonRepository(tmp_path / "boom.json"), mock, embedder=_BoomEmbedder())
    state = core.reply("привіт", core.start_session())             # the recall call hits the embedder error
    assert not is_trusted_text(mock.tool_calls[0][2])              # degraded to a plain notice, not a crash
    assert state.emotion == "calm"                                # the turn completes; emotion contract holds

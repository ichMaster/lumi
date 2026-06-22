"""v0.36 LUMI-141 — fact embedding + `recall(scope=facts)`.

Each `LongTermFact` is embedded into the per-user vector store as a `kind="fact"` vector (on write +
a backfill); `recall`/`recall_moments`/the recall tool gain a `scope` (messages | facts | all) that
routes the cosine search to one layer. `scope="messages"` (the default) stays byte-identical to v0.16
— facts never leak into the message paths. MockEmbedder — no paid calls."""
from __future__ import annotations

from core.agent import Core
from core.embedder import MockEmbedder
from core.llm import MockLLMClient, is_trusted_text
from core.repository import LongTermFact, fact_vector_id
from state.local_store import JsonRepository


def _core(tmp_path, *, user="owner", store="store.json"):
    return Core(
        llm=MockLLMClient("ок"),
        repository=JsonRepository(tmp_path / store),
        canon="C", model="m", user_id=user,
        embedder=MockEmbedder(), recall_enabled=True, embed_model="m@x",
        recall_tool_enabled=True, recall_tool_k=5, recall_tool_max_calls=3,
        rag_enabled=True, rag_k=5, rag_floor=0.0, rag_max_chars=8000, rag_snippet_chars=4000,
    )


def _add_fact(core, text, user="owner"):
    core._repo.add_fact(LongTermFact(user_id=user, fact=text, meta="", confidence=0.5,
                                     ts="2026-06-20T00:00:00+00:00"))


def test_fact_is_embedded_as_fact_kind(tmp_path):
    core = _core(tmp_path)
    _add_fact(core, "любить каву")
    assert core.backfill_facts() == 1                       # the fact gets embedded
    assert core._repo.has_vector("owner", fact_vector_id("owner", "любить каву"))
    assert core.backfill_facts() == 0                       # idempotent — re-backfill is a no-op


def test_recall_scope_facts_finds_the_fact(tmp_path):
    core = _core(tmp_path)
    _add_fact(core, "любить каву")
    core.backfill_facts()
    hits = core.recall("каву", scope="facts")
    assert hits and any(r.kind == "fact" and "каву" in r.text for _, r in hits)


def test_recall_scope_messages_excludes_facts(tmp_path):
    # The default scope is messages-only — a fact never surfaces in the message path (byte-identical).
    core = _core(tmp_path)
    _add_fact(core, "любить каву")
    core.backfill_facts()
    assert core.recall("каву", scope="messages") == []     # no message vectors → nothing
    # ...but it IS findable via the facts scope.
    assert core.recall("каву", scope="facts")


def test_recall_scope_all_spans_both_layers(tmp_path):
    core = _core(tmp_path)
    s = core.start_session()
    core.reply("я люблю каву вранці", s)        # a message mentioning каву (indexed on write)
    _add_fact(core, "любить каву")              # a fact mentioning каву
    core.backfill_facts()                       # embed the fact (reply() already drained the msg backfill)
    kinds = {r.kind for _, r in core.recall("каву", scope="all", k=10)}
    assert kinds == {"message", "fact"}         # both layers present


def test_recall_tool_scope_facts(tmp_path):
    core = _core(tmp_path)
    _add_fact(core, "любить каву")
    core.ensure_backfill()
    _, execute = core._recall_tool_args()
    out = execute("recall", {"query": "каву", "scope": "facts"})
    assert is_trusted_text(out) and "каву" in out["text"]   # her own fact, trusted framing


def test_fact_recall_is_per_user_isolated(tmp_path):
    # A fact written under owner never surfaces in another user's recall (the isolation invariant).
    core = _core(tmp_path)
    _add_fact(core, "любить каву", user="owner")
    core.backfill_facts()
    other = Core(
        llm=MockLLMClient("ок"), repository=core._repo,
        canon="C", model="m", user_id="stranger",
        embedder=MockEmbedder(), recall_enabled=True, embed_model="m@x",
    )
    assert other.recall("каву", scope="facts") == []        # никого чужого
    assert other.recall("каву", scope="all") == []

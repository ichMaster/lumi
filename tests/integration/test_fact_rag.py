"""v0.36 LUMI-144 — the per-turn fact-RAG push (`# Релевантні факти`).

Like the v0.17 message auto-RAG, but over the fact vectors: each turn embeds the incoming message →
top-K relevant **non-core** facts → a `# Релевантні факти` block in the volatile tail. Deduped against
the core block (a core fact is never re-pushed); off → no block. Model + embedder mocked."""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.embedder import MockEmbedder
from core.llm import MockLLMClient
from core.repository import LongTermFact
from state.local_store import JsonRepository

NOW = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)


def _core(tmp_path, *, facts_rag=False, facts_rag_k=4, embedder="mock", recall=True, user="owner"):
    return Core(
        llm=MockLLMClient("ок"), repository=JsonRepository(tmp_path / "s.json"),
        canon="C", model="m", user_id=user, clock=fixed_clock(NOW),
        mood_enabled=False, biorhythms_enabled=False, cycle_enabled=False,
        closeness_enabled=False, thoughts_enabled=False,
        embedder=MockEmbedder() if embedder == "mock" else embedder,
        recall_enabled=recall, embed_model="m@x", rag_enabled=True, rag_floor=0.0,
        facts_rag=facts_rag, facts_rag_k=facts_rag_k,
    )


def _fact(text, *, core=False, user="owner"):
    return LongTermFact(user_id=user, fact=text, meta="", confidence=1.0, ts=NOW.isoformat(), core=core)


def test_fact_rag_injects_relevant_non_core_facts(tmp_path):
    core = _core(tmp_path, facts_rag=True)
    core._repo.add_fact(_fact("Любить мандарини взимку", core=False))
    core.backfill_facts()
    block = core._fact_recall_block("мандарини")
    assert block and "мандарини" in block


def test_fact_rag_excludes_core_facts(tmp_path):
    # A core fact is already in the static block — never re-pushed by the auto-RAG.
    core = _core(tmp_path, facts_rag=True)
    core._repo.add_fact(_fact("Любить мандарини взимку", core=True))
    core.backfill_facts()
    assert core._fact_recall_block("мандарини") is None


def test_fact_rag_off_no_block(tmp_path):
    core = _core(tmp_path, facts_rag=False)
    core._repo.add_fact(_fact("Любить мандарини", core=False))
    core.backfill_facts()
    assert core._fact_recall_block("мандарини") is None


def test_fact_rag_section_renders_in_the_prompt_tail(tmp_path):
    core = _core(tmp_path, facts_rag=True)
    core._repo.add_fact(_fact("Любить мандарини взимку", core=False))
    core.backfill_facts()
    s = core.start_session()
    system, cache_prefix = core._system_prompt(s, fact_recall=core._fact_recall_block("мандарини"))
    assert "# Релевантні факти" in system and "мандарини" in system
    assert "# Релевантні факти" not in cache_prefix          # the block is in the volatile tail, not cached


def test_fact_rag_caps_at_k(tmp_path):
    core = _core(tmp_path, facts_rag=True, facts_rag_k=2)
    for i in range(5):
        core._repo.add_fact(_fact(f"любить число {i} дуже сильно", core=False))
    core.backfill_facts()
    block = core._fact_recall_block("любить число")
    assert block and len([ln for ln in block.splitlines() if ln.startswith("- ")]) <= 2


def test_fact_rag_no_embedder_degrades_to_no_block(tmp_path):
    core = _core(tmp_path, facts_rag=True, embedder=None, recall=False)
    core._repo.add_fact(_fact("Любить мандарини", core=False))
    assert core._fact_recall_block("мандарини") is None     # best-effort, never raises


def test_fact_rag_is_per_user_isolated(tmp_path):
    core = _core(tmp_path, facts_rag=True, user="owner")
    core._repo.add_fact(_fact("Любить мандарини", core=False, user="owner"))
    core.backfill_facts()
    other = Core(
        llm=MockLLMClient("ок"), repository=core._repo, canon="C", model="m", user_id="stranger",
        clock=fixed_clock(NOW), embedder=MockEmbedder(), recall_enabled=True, embed_model="m@x",
        rag_enabled=True, rag_floor=0.0, facts_rag=True,
    )
    assert other._fact_recall_block("мандарини") is None    # owner's facts never reach stranger

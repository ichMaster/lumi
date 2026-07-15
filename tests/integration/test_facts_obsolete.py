"""v0.36 LUMI-145 — facts hygiene: the `obsolete` flag filtered out of every fact path.

A fact marked `obsolete=true` is excluded from the core static block, the auto fact-RAG, AND
`recall(scope=facts)` — but kept in the store (non-destructive, reversible, auditable). The
`/review-facts` Claude Code skill proposes the obsolete set for human review (offline). Mocked."""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.embedder import MockEmbedder
from core.llm import MockLLMClient
from core.repository import LongTermFact
from state.local_store import JsonRepository

NOW = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)


def _core(tmp_path, *, facts_rag=False):
    return Core(
        llm=MockLLMClient("ок"), repository=JsonRepository(tmp_path / "s.json"),
        canon="C", model="m", user_id="owner", clock=fixed_clock(NOW),
        mood_enabled=False, biorhythms_enabled=False, cycle_enabled=False,
        closeness_enabled=False, thoughts_enabled=False,
        embedder=MockEmbedder(), recall_enabled=True, embed_model="m@x",
        recall_tool_enabled=True, rag_enabled=True, rag_floor=0.0,
        facts_enabled=True, facts_core_max=0, facts_rag=facts_rag,
    )


def _fact(text, *, core=False, obsolete=False):
    return LongTermFact(user_id="owner", fact=text, meta="", confidence=1.0, ts=NOW.isoformat(),
                        core=core, obsolete=obsolete)


def test_obsolete_excluded_from_recall_facts(tmp_path):
    core = _core(tmp_path)
    core._repo.add_fact(_fact("Любить мандарини взимку", obsolete=True))
    core.backfill_facts()
    assert core.recall("мандарини", scope="facts") == []        # obsolete never surfaces in recall


def test_obsolete_excluded_from_auto_fact_rag(tmp_path):
    core = _core(tmp_path, facts_rag=True)
    core._repo.add_fact(_fact("Любить мандарини взимку", obsolete=True))
    core.backfill_facts()
    assert core._fact_recall_block("мандарини") is None         # not pushed


def test_obsolete_excluded_from_core_block(tmp_path):
    core = _core(tmp_path)
    core._repo.add_fact(_fact("Звати Олег", core=True))
    core._repo.add_fact(_fact("Старий факт", core=True, obsolete=True))  # core BUT obsolete
    prompt = "".join(core._system_prompt(core.start_session()))
    assert "Звати Олег" in prompt and "Старий факт" not in prompt


def test_non_obsolete_facts_unaffected(tmp_path):
    core = _core(tmp_path)
    core._repo.add_fact(_fact("Любить мандарини взимку", obsolete=False))
    core.backfill_facts()
    assert core.recall("мандарини", scope="facts")             # a live fact still surfaces


def test_set_fact_obsolete_persists_and_is_reversible(tmp_path):
    repo = JsonRepository(tmp_path / "s.json")
    repo.add_fact(_fact("Старий факт"))
    repo.set_fact_obsolete("owner", "Старий факт", True)
    assert JsonRepository(tmp_path / "s.json").facts("owner")[0].obsolete is True   # persists
    repo.set_fact_obsolete("owner", "Старий факт", False)                           # reversible
    assert JsonRepository(tmp_path / "s.json").facts("owner")[0].obsolete is False


def test_obsolete_kept_in_store_non_destructive(tmp_path):
    core = _core(tmp_path)
    core._repo.add_fact(_fact("Старий факт", obsolete=True))
    assert [f.fact for f in core._repo.facts("owner")] == ["Старий факт"]  # still in the store (audit)

"""v0.36 LUMI-143 — the facts block is the curated identity-core.

`LUMI_FACTS` is the master switch: on → the `## Про Віталія` section injects the `core=true` facts
(capped by `LUMI_FACTS_CORE_MAX`); off → no static facts section at all. The core is curated by the
offline /review-facts skill (there is no in-app re-rank, and no digest/raw fallback). The episodic
tail (non-core facts) stays reachable via `recall(scope=facts)`. Model + embedder mocked."""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.embedder import MockEmbedder
from core.llm import MockLLMClient
from core.repository import LongTermFact
from state.local_store import JsonRepository

NOW = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)


def _core(tmp_path, *, enabled=True, core_max=0, embedder=None, recall=False, recall_scope="messages"):
    return Core(
        llm=MockLLMClient("ок"), repository=JsonRepository(tmp_path / "s.json"),
        canon="C", model="m", user_id="owner", clock=fixed_clock(NOW),
        mood_enabled=False, biorhythms_enabled=False, cycle_enabled=False,
        closeness_enabled=False, thoughts_enabled=False,
        embedder=embedder, recall_enabled=recall, embed_model="m@x",
        recall_tool_enabled=recall, facts_enabled=enabled, facts_core_max=core_max,
        recall_scope=recall_scope,
    )


def _fact(text, *, core=False):
    return LongTermFact(user_id="owner", fact=text, meta="", confidence=1.0, ts=NOW.isoformat(), core=core)


def _prompt(core, session):
    return "".join(core._system_prompt(session))


def test_facts_on_injects_only_core_facts(tmp_path):
    core = _core(tmp_path, enabled=True)
    core._repo.add_fact(_fact("Звати Олег", core=True))
    core._repo.add_fact(_fact("Якось згадав дрібницю", core=False))
    prompt = _prompt(core, core.start_session())
    assert "## Про Віталія" in prompt              # the section renders
    assert "Звати Олег" in prompt                  # the identity-core is injected
    assert "Якось згадав дрібницю" not in prompt   # the non-core tail is not


def test_facts_caps_to_core_max(tmp_path):
    # All facts flagged core (a heavily-pinned set), but CORE_MAX=2 → only 2 reach the prompt.
    core = _core(tmp_path, enabled=True, core_max=2)
    for i in range(5):
        core._repo.add_fact(_fact(f"домовленість номер {i}", core=True))
    prompt = _prompt(core, core.start_session())
    assert sum(1 for i in range(5) if f"домовленість номер {i}" in prompt) == 2  # hard-capped, not all 5


def test_core_max_zero_injects_all_core_facts(tmp_path):
    # CORE_MAX=0 (cap off) → every core fact is injected.
    core = _core(tmp_path, enabled=True, core_max=0)
    for i in range(4):
        core._repo.add_fact(_fact(f"межа {i}", core=True))
    prompt = _prompt(core, core.start_session())
    assert all(f"межа {i}" in prompt for i in range(4))


def test_facts_off_no_static_section(tmp_path):
    # LUMI_FACTS=off → the ## Про Віталія section is skipped entirely, even with core facts present.
    core = _core(tmp_path, enabled=False)
    core._repo.add_fact(_fact("Звати Олег", core=True))
    core._repo.add_fact(_fact("Любить мандарини", core=False))
    prompt = _prompt(core, core.start_session())
    assert "## Про Віталія" not in prompt
    assert "Звати Олег" not in prompt


def test_facts_on_no_core_facts_no_section(tmp_path):
    # On, but nothing flagged core → the section is empty (no digest/raw fallback anymore).
    core = _core(tmp_path, enabled=True)
    core._repo.add_fact(_fact("Любить мандарини", core=False))
    prompt = _prompt(core, core.start_session())
    assert "## Про Віталія" not in prompt          # no core → no section
    assert "Любить мандарини" not in prompt        # non-core is never in the static block


def test_non_core_tail_reachable_via_recall_facts(tmp_path):
    # A non-core fact is absent from the prompt but stays findable via recall(scope=facts).
    core = _core(tmp_path, enabled=True, embedder=MockEmbedder(), recall=True)
    core._repo.add_fact(_fact("Звати Олег", core=True))
    core._repo.add_fact(_fact("Любить мандарини взимку", core=False))
    core.backfill_facts()
    prompt = _prompt(core, core.start_session())
    assert "Звати Олег" in prompt and "мандарини" not in prompt      # tail not in the prompt
    hits = core.recall("мандарини", scope="facts")
    assert any("мандарини" in r.text for _, r in hits)              # ...but reachable on demand


def test_recall_tool_default_scope_from_config(tmp_path):
    # LUMI_RECALL_SCOPE=all → the recall tool searches facts too when the model omits scope.
    core = _core(tmp_path, embedder=MockEmbedder(), recall=True, recall_scope="all")
    core._repo.add_fact(_fact("Любить мандарини взимку", core=False))
    core.backfill_facts()
    _, execute = core._recall_tool_args()
    out = execute("recall", {"query": "мандарини"})                # no scope → config default "all"
    assert isinstance(out, dict) and "мандарини" in out["text"]    # the fact surfaces via the default scope

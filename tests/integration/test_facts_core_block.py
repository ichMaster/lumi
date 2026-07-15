"""v0.36 LUMI-143 — shrink the facts block to the curated identity-core.

With `LUMI_FACTS_CORE_ONLY` on, the prompt injects only the `core=true` facts (replacing the digest —
and the session-start re-flag replaces the digest *call*, cost-neutral); the episodic tail moves to
`recall(scope=facts)`. Off → the Phase-0 digest/raw facts, byte-identical. Model + embedder mocked."""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.embedder import MockEmbedder
from core.llm import MockLLMClient
from core.repository import LongTermFact
from state.local_store import JsonRepository

NOW = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)


def _core(tmp_path, *, core_only=False, core_max=0, embedder=None, recall=False, recall_scope="messages"):
    return Core(
        llm=MockLLMClient("ок"), repository=JsonRepository(tmp_path / "s.json"),
        canon="C", model="m", user_id="owner", clock=fixed_clock(NOW),
        mood_enabled=False, biorhythms_enabled=False, cycle_enabled=False,
        closeness_enabled=False, thoughts_enabled=False,
        embedder=embedder, recall_enabled=recall, embed_model="m@x",
        recall_tool_enabled=recall, facts_core_only=core_only, facts_core_max=core_max,
        recall_scope=recall_scope,
    )


def _fact(text, *, core=False):
    return LongTermFact(user_id="owner", fact=text, meta="", confidence=1.0, ts=NOW.isoformat(), core=core)


def _prompt(core, session):
    return "".join(core._system_prompt(session))


def test_core_only_injects_only_core_facts(tmp_path):
    core = _core(tmp_path, core_only=True)
    core._repo.add_fact(_fact("Звати Олег", core=True))
    core._repo.add_fact(_fact("Якось згадав дрібницю", core=False))
    prompt = _prompt(core, core.start_session())
    assert "Звати Олег" in prompt                 # the identity-core is injected
    assert "Якось згадав дрібницю" not in prompt   # the non-core tail is not


def test_core_only_caps_prompt_to_core_max(tmp_path):
    # All facts flagged core (as a heavily-pinned set would be), but CORE_MAX=2 → only 2 reach the prompt.
    core = _core(tmp_path, core_only=True, core_max=2)
    for i in range(5):
        core._repo.add_fact(_fact(f"домовленість номер {i}", core=True))  # all core (would all pin)
    prompt = _prompt(core, core.start_session())
    injected = sum(1 for i in range(5) if f"домовленість номер {i}" in prompt)
    assert injected == 2  # hard-capped to CORE_MAX, not all 5


def test_core_max_zero_injects_all_core_facts(tmp_path):
    # CORE_MAX=0 (cap off) keeps the old behaviour: every core fact is injected.
    core = _core(tmp_path, core_only=True, core_max=0)
    for i in range(4):
        core._repo.add_fact(_fact(f"межа {i}", core=True))
    prompt = _prompt(core, core.start_session())
    assert all(f"межа {i}" in prompt for i in range(4))


def test_core_only_off_injects_all_facts_unchanged(tmp_path):
    core = _core(tmp_path, core_only=False)
    core._repo.add_fact(_fact("Звати Олег", core=True))
    core._repo.add_fact(_fact("Любить мандарини", core=False))
    prompt = _prompt(core, core.start_session())
    assert "Звати Олег" in prompt and "Любить мандарини" in prompt   # both (off → raw facts)


def test_core_only_falls_back_when_no_core_facts(tmp_path):
    # core_only on but nothing flagged yet → fall back to raw (never an empty facts block).
    core = _core(tmp_path, core_only=True)
    core._repo.add_fact(_fact("Любить мандарини", core=False))
    assert "Любить мандарини" in _prompt(core, core.start_session())


def test_reconstruction_dropped_tail_reachable_via_recall_facts(tmp_path):
    # A non-core fact is dropped from the prompt but stays findable via recall(scope=facts).
    core = _core(tmp_path, core_only=True, embedder=MockEmbedder(), recall=True)
    core._repo.add_fact(_fact("Звати Олег", core=True))
    core._repo.add_fact(_fact("Любить мандарини взимку", core=False))
    core.backfill_facts()
    prompt = _prompt(core, core.start_session())
    assert "Звати Олег" in prompt and "мандарини" not in prompt      # tail not in the prompt
    hits = core.recall("мандарини", scope="facts")
    assert any("мандарини" in r.text for _, r in hits)              # ...but reachable on demand


def test_core_only_skips_the_digest_call(tmp_path):
    # core_only replaces _ensure_facts_digest (cost-neutral with the session-start re-flag).
    core = _core(tmp_path, core_only=True)
    calls = {"n": 0}
    core._ensure_facts_digest = lambda: calls.__setitem__("n", calls["n"] + 1)  # type: ignore[method-assign]
    s = core.start_session()
    core.reply("привіт", s)
    assert calls["n"] == 0                                          # digest never called when core_only on


def test_core_only_off_still_calls_the_digest(tmp_path):
    core = _core(tmp_path, core_only=False)
    calls = {"n": 0}
    core._ensure_facts_digest = lambda: calls.__setitem__("n", calls["n"] + 1)  # type: ignore[method-assign]
    core.reply("привіт", core.start_session())
    assert calls["n"] == 1                                          # unchanged when core_only off


def test_recall_tool_default_scope_from_config(tmp_path):
    # LUMI_RECALL_SCOPE=all → the recall tool searches facts too when the model omits scope.
    core = _core(tmp_path, embedder=MockEmbedder(), recall=True, recall_scope="all")
    core._repo.add_fact(_fact("Любить мандарини взимку", core=False))
    core.backfill_facts()
    _, execute = core._recall_tool_args()
    out = execute("recall", {"query": "мандарини"})                # no scope → config default "all"
    assert isinstance(out, dict) and "мандарини" in out["text"]    # the fact surfaces via the default scope

"""v0.36 LUMI-142 — the `core` (identity-core) flag.

`LongTermFact.core` is set by the `[C]` marker at extraction (an initial guess) and, authoritatively,
by the offline **/review-facts** skill (Opus). There is **no in-app session-start re-rank** — a weak
housekeeping model was silently collapsing curated pins, so that logic was removed; the skill is the
sole reranker. The core facts (up to `LUMI_FACTS_CORE_MAX`) are injected into `## Про Віталія`. Model mocked."""
from __future__ import annotations

from types import SimpleNamespace

from core.agent import Core
from core.llm import MockLLMClient
from core.repository import LongTermFact
from state.local_store import JsonRepository

TS = "2026-06-20T00:00:00+00:00"


def _core(tmp_path, *, reply="ок", core_max=2):
    return Core(
        llm=MockLLMClient(reply),
        repository=JsonRepository(tmp_path / "store.json"),
        canon="C", model="m", user_id="owner",
        recall_enabled=False, facts_core_max=core_max,
    )


def _fact(text, *, core=False, user="owner"):
    return LongTermFact(user_id=user, fact=text, meta="", confidence=0.5, ts=TS, core=core)


def test_extraction_marks_core_from_the_marker(tmp_path):
    # The [C] marker on an extracted line → core=True; an unmarked line → core=False.
    core = _core(tmp_path, reply="[C] Звати Олег\nЛюбить піцу")
    core._accumulate_facts([SimpleNamespace(role="user", text="...")])
    flags = {f.fact: f.core for f in core._repo.facts("owner")}
    assert flags == {"Звати Олег": True, "Любить піцу": False}


def test_session_start_never_touches_core_flags(tmp_path):
    # No in-app re-rank: a curated core (even one that exceeds the cap) is left EXACTLY as the skill set
    # it — a weak model call can never demote a pin. Also: no model call at session start for the core.
    called = {"n": 0}

    def watch(system, messages, model):
        called["n"] += 1
        return "Любить каву"

    core = _core(tmp_path, reply=watch, core_max=2)
    for t in ("Любить каву", "Має кота", "Полюбляє джаз", "Слухає джаз"):  # 4 core > cap 2
        core._repo.add_fact(_fact(t, core=True))
    core.start_session()
    cores = {f.fact for f in core._repo.facts("owner") if f.core}
    assert cores == {"Любить каву", "Має кота", "Полюбляє джаз", "Слухає джаз"}  # untouched
    assert not hasattr(core, "_ensure_core_flags")  # the method is gone


def test_core_flag_persists_across_reload(tmp_path):
    repo = JsonRepository(tmp_path / "store.json")
    repo.add_fact(_fact("Звати Олег", core=True))
    repo.add_fact(_fact("Любить піцу", core=False))
    reloaded = JsonRepository(tmp_path / "store.json")
    assert {f.fact: f.core for f in reloaded.facts("owner")} == {"Звати Олег": True, "Любить піцу": False}

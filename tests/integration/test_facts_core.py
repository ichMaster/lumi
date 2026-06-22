"""v0.36 LUMI-142 — the `core` (identity-core) flag + its lifecycle.

`LongTermFact.core` is curated three ways: a one-off **backfill** (first run → pick from all facts),
an **initial guess at extraction** (the `[C]` marker on a new fact), and a **session-start re-flag**
that re-ranks the `core=true` pool to `LUMI_FACTS_CORE_MAX` with **boundaries pinned**. The re-flag's
input is the small core pool (not all facts). Model mocked — no paid calls."""
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


def test_session_start_reflag_caps_and_pins_boundary(tmp_path):
    # > N core facts incl. a boundary → cap to the model's top-N, but the boundary is PINNED.
    core = _core(tmp_path, reply="Любить каву\nМає кота", core_max=2)
    for t in ("Любить каву", "Має кота", "Полюбляє джаз",
              "Ми домовились ніколи не дзвонити після 22:00"):
        core._repo.add_fact(_fact(t, core=True))
    core.start_session()
    cores = {f.fact for f in core._repo.facts("owner") if f.core}
    assert "Ми домовились ніколи не дзвонити після 22:00" in cores  # boundary pinned (kept past the cap)
    assert "Полюбляє джаз" not in cores                            # dropped — not in the model's top-2
    assert {"Любить каву", "Має кота"} <= cores                    # the model's chosen survive


def test_reflag_input_is_only_the_core_pool(tmp_path):
    # The re-flag sends ONLY core=true facts to the model — not the whole (large) facts list.
    captured = {}

    def chooser(system, messages, model):
        captured["content"] = messages[-1]["content"]
        return "Любить каву"

    core = _core(tmp_path, reply=chooser, core_max=3)
    core._repo.add_fact(_fact("Любить каву", core=True))
    core._repo.add_fact(_fact("Випадковий дрібний факт", core=False))
    core.start_session()
    assert "Любить каву" in captured["content"]
    assert "Випадковий дрібний факт" not in captured["content"]   # non-core never sent


def test_backfill_first_run_selects_from_all_facts(tmp_path):
    # Nothing flagged yet → the pool is ALL facts (the one-off backfill that seeds the core).
    captured = {}

    def chooser(system, messages, model):
        captured["content"] = messages[-1]["content"]
        return "Звати Олег"

    core = _core(tmp_path, reply=chooser, core_max=1)
    core._repo.add_fact(_fact("Звати Олег"))
    core._repo.add_fact(_fact("Любить піцу"))
    core.start_session()
    assert "Звати Олег" in captured["content"] and "Любить піцу" in captured["content"]  # all sent
    assert {f.fact for f in core._repo.facts("owner") if f.core} == {"Звати Олег"}


def test_reflag_off_when_cap_zero(tmp_path):
    core = _core(tmp_path, reply="x", core_max=0)
    core._repo.add_fact(_fact("Любить каву", core=True))
    core.start_session()
    assert core._llm.calls == []                                  # no model call when the cap is 0
    assert [f.core for f in core._repo.facts("owner")] == [True]  # flags untouched


def test_core_flag_persists_across_reload(tmp_path):
    repo = JsonRepository(tmp_path / "store.json")
    repo.add_fact(_fact("Звати Олег", core=True))
    repo.add_fact(_fact("Любить піцу", core=False))
    reloaded = JsonRepository(tmp_path / "store.json")
    assert {f.fact: f.core for f in reloaded.facts("owner")} == {"Звати Олег": True, "Любить піцу": False}


def test_reflag_is_per_user_isolated(tmp_path):
    # owner's re-flag never touches another user's facts.
    core = _core(tmp_path, reply="Любить каву", core_max=1)
    core._repo.add_fact(_fact("Любить каву", core=True, user="owner"))
    core._repo.add_fact(_fact("Любить чай", core=True, user="stranger"))
    core.start_session()
    assert any(f.core for f in core._repo.facts("stranger"))      # stranger's flag untouched

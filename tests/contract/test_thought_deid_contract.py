"""v0.33 LUMI-128 — contract: a thought-driven external query is de-identified; %prompt is exempt.

A planted private detail never leaves for the external service (wiki/news/web/imagine); the owner's
%prompt instruction passes through. Mock model — no paid calls.
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.deidentify import REDACTION
from core.llm import MockLLMClient
from core.repository import LongTermFact
from core.thoughts import Directive
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 6, 21, 12, 0, tzinfo=UTC))


def _core(tmp_path, user="owner"):
    return Core(llm=MockLLMClient("x"), repository=JsonRepository(tmp_path / "s.json"),
                canon="C", model="m", clock=_CLK, mood_enabled=False, closeness_enabled=False,
                thoughts_enabled=True, thought_tools_enabled=True, user_id=user,
                file_tool_enabled=True, files_dir=tmp_path / "files")


def _plant_fact(core, text, user="owner"):
    core._repo.add_fact(LongTermFact(user, text, "", 1.0, "2026-06-20T10:00"))


def test_deidentify_external_redacts_a_planted_personal_term(tmp_path):
    core = _core(tmp_path)
    _plant_fact(core, "користувача звати Олег")
    out = core._deidentify_external("розкажи про Олега та каву")
    assert "Олег" not in out and REDACTION in out and "каву" in out  # only the topical part leaves


def test_deidentified_wrapper_redacts_external_args_keeps_others(tmp_path):
    core = _core(tmp_path)
    _plant_fact(core, "звати Олег")
    seen = {}

    def fake_exec(name, inp):
        seen[name] = dict(inp)
        return "ok"

    wrapped = core._deidentified(fake_exec)
    wrapped("wiki_search", {"query": "про Олега"})            # external query → de-identified
    wrapped("generate_image", {"prompt": "Олег під дощем"})    # external gen prompt → de-identified
    wrapped("list_files", {"path": "Олег"})                    # NOT external → untouched
    assert "Олег" not in seen["wiki_search"]["query"] and REDACTION in seen["wiki_search"]["query"]
    assert "Олег" not in seen["generate_image"]["prompt"]
    assert seen["list_files"]["path"] == "Олег"                # a non-external arg passes through


def test_prompt_directive_is_exempt_from_deidentification(tmp_path, monkeypatch):
    core = _core(tmp_path)
    _plant_fact(core, "звати Олег")
    seen = []

    def fake_exec(name, inp):
        seen.append(inp.get("query"))
        return "ok"

    monkeypatch.setattr(core, "_turn_tools", lambda: ([{"name": "wiki_search"}], fake_exec))
    _, ext = core._thought_tools(Directive("ext", "...", tools=("wiki_search",)))
    ext("wiki_search", {"query": "про Олега"})                  # normal external → de-identified
    _, pr = core._thought_tools(
        Directive("pr", "...", tools=("wiki_search",), instruction_from_topic=True))
    pr("wiki_search", {"query": "про Олега"})                   # %prompt → exempt, passes through
    assert "Олег" not in seen[0] and REDACTION in seen[0]
    assert seen[1] == "про Олега"


def test_deid_is_per_user_isolated(tmp_path):
    # B's de-id set is built from B's OWN facts — A's planted name is not B's personal term.
    core_a = _core(tmp_path, user="alice")
    _plant_fact(core_a, "звати Олег", user="alice")
    core_b = _core(tmp_path, user="bob")
    out = core_b._deidentify_external("про Олега")  # bob has no such fact → not his personal term
    assert out == "про Олега"  # B never inherits A's de-id terms (isolation)

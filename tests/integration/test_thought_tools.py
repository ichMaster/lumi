"""v0.33 LUMI-126 — think() routes through the think-path tool-loop when a directive opts into tools.

Gated by LUMI_THOUGHT_TOOLS; %think/%wonder (tools=()) stay the v0.12 tool-less call. Mock model — no paid calls.
"""
from __future__ import annotations

from datetime import UTC, datetime

import core.thoughts as thoughts_mod
from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from core.thoughts import Directive
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 6, 21, 12, 0, tzinfo=UTC))


def _core(tmp_path, mock, *, thought_tools=True, file_tool=True):
    return Core(
        llm=mock, repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.", model="m", clock=_CLK, user_id="owner",
        mood_enabled=False, closeness_enabled=False,
        thoughts_enabled=True, thought_tools_enabled=thought_tools,
        file_tool_enabled=file_tool, files_dir=tmp_path / "files",
    )


def _sandbox(tmp_path, name, text):
    root = tmp_path / "files" / "owner"
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text, encoding="utf-8")


def test_tool_directive_runs_the_loop_and_records_a_thought(tmp_path, monkeypatch):
    _sandbox(tmp_path, "notes.md", "рядок\n")
    monkeypatch.setitem(thoughts_mod.REGISTRY, "tooly",
                        Directive("tooly", "поміркуй із файлами", tools=("list_files",), cap=3))
    mock = MockLLMClient("Глянула — є нотатки.\nЕМОЦІЯ: calm", tool_script=[("list_files", {})])
    core = _core(tmp_path, mock)
    thought = core.think("tooly")
    assert thought is not None and thought.kind == "tooly"
    assert thought.text == "Глянула — є нотатки." and thought.emotion == "calm"  # ЕМОЦІЯ stripped
    assert [c[0] for c in mock.tool_calls] == ["list_files"]      # the tool ran in the think loop
    # the loop used reply() (text terminal), never reply_structured — no set_state turn for a thought
    assert mock.calls and len(mock.tool_calls) == 1


def test_tool_directive_is_tool_less_when_master_gate_off(tmp_path, monkeypatch):
    _sandbox(tmp_path, "notes.md", "рядок\n")
    monkeypatch.setitem(thoughts_mod.REGISTRY, "tooly",
                        Directive("tooly", "...", tools=("list_files",)))
    mock = MockLLMClient("думка\nЕМОЦІЯ: calm", tool_script=[("list_files", {})])
    core = _core(tmp_path, mock, thought_tools=False)            # master gate off
    thought = core.think("tooly")
    assert thought is not None and thought.text == "думка"
    assert mock.tool_calls == []                                 # no tools offered → script not run


def test_think_and_wonder_stay_tool_less_with_gate_on(tmp_path):
    mock = MockLLMClient("тиха думка\nЕМОЦІЯ: calm", tool_script=[("list_files", {})])
    core = _core(tmp_path, mock)                                 # thought_tools ON, file tool ON
    core.think("think")
    assert mock.tool_calls == []                                 # THINK has tools=() → tool-less, unchanged


def test_user_typed_topic_survives_external_deid(tmp_path, monkeypatch):
    # end-to-end: run a tool-thought whose external query echoes the user's typed place — user_topic=True
    # threads the typed words into the de-id keep whitelist, so the city is NOT redacted out of the query.
    from core.repository import LongTermFact
    monkeypatch.setitem(thoughts_mod.REGISTRY, "evt",
                        Directive("evt", "глянь, що в місті", tools=("web_lookup",), family="web"))
    seen: list[str] = []
    monkeypatch.setattr(
        "core.agent.Core._turn_tools",
        lambda self: ([{"name": "web_lookup"}], lambda n, i: (seen.append(i.get("query")), "ok")[1]),
    )
    mock = MockLLMClient("глянула — тихо й людно.\nЕМОЦІЯ: calm",
                         tool_script=[("web_lookup", {"query": "події у Львові наступний тиждень"})])
    core = _core(tmp_path, mock)
    core._repo.add_fact(LongTermFact("owner", "живе у Львові", "", 1.0, "2026-06-20T10:00"))
    core.think("evt", topic="події у Львові наступний тиждень", user_topic=True)
    assert seen and seen[0] == "події у Львові наступний тиждень"   # the user's typed city survived
    core.think("evt", topic="події у Львові наступний тиждень")     # autonomous → no keep
    assert "Львові" not in seen[1]                                  # her inner-state query is de-identified


def test_prompt_is_freeform_records_full_length_output(tmp_path):
    # %prompt is the OPEN directive: a multi-fact / long-analysis ask must NOT be squeezed to a 1–2 sentence
    # musing. The freeform template lets the output match the instruction; the sink saves it in full.
    long = ("Факт 1: хвіст комети завжди спрямований геть від Сонця. Факт 2: ядро комети — це лід і пил. "
            "Факт 3: комета Галлея повертається приблизно кожні 76 років.")
    core = Core(
        llm=MockLLMClient(f"{long}\nЕМОЦІЯ: thoughtful"), repository=JsonRepository(tmp_path / "s.json"),
        canon="Ти — Лілі.", model="m", clock=_CLK, user_id="owner",
        mood_enabled=False, closeness_enabled=False, thoughts_enabled=True,
        thought_tools_enabled=True, thought_prompt=True, file_tool_enabled=True, files_dir=tmp_path / "files",
    )
    out = core.run_directive("%prompt знайди три факти про комети", core.start_session())
    assert out.is_directive and out.thought is not None
    assert out.thought.text == long                          # the full 3-fact answer recorded, not capped
    out2 = core.run_directive("%prompt >notes знайди три факти про комети", core.start_session())
    assert out2.saved_to == "notes/2026-06-21.md"            # >notes saves the full-length text too
    assert long in (tmp_path / "files" / "owner" / "notes" / "2026-06-21.md").read_text(encoding="utf-8")


def test_unknown_named_tool_falls_back_to_tool_less(tmp_path, monkeypatch):
    monkeypatch.setitem(thoughts_mod.REGISTRY, "tooly",
                        Directive("tooly", "...", tools=("no_such_tool",)))
    mock = MockLLMClient("думка\nЕМОЦІЯ: calm", tool_script=[("list_files", {})])
    core = _core(tmp_path, mock)                                 # no matching enabled tool → tool-less
    core.think("tooly")
    assert mock.tool_calls == []

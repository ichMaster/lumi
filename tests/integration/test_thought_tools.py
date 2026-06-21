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


def test_unknown_named_tool_falls_back_to_tool_less(tmp_path, monkeypatch):
    monkeypatch.setitem(thoughts_mod.REGISTRY, "tooly",
                        Directive("tooly", "...", tools=("no_such_tool",)))
    mock = MockLLMClient("думка\nЕМОЦІЯ: calm", tool_script=[("list_files", {})])
    core = _core(tmp_path, mock)                                 # no matching enabled tool → tool-less
    core.think("tooly")
    assert mock.tool_calls == []

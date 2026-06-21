"""v0.33 LUMI-134 — memory %recall (inward, trusted) + open %prompt (owner-only, topic-as-instruction).

%recall runs the v0.31 recall tool in the think path (no de-id, results trusted); %prompt is owner-only and
turns the topic into the directive instruction. Mock model + mock embedder — no paid calls.
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.embedder import MockEmbedder
from core.llm import MockLLMClient
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 6, 21, 12, 0, tzinfo=UTC))


def _core(tmp_path, mock, *, master=True, recall_tool=True, thought_prompt=True, file_tool=True):
    return Core(
        llm=mock, repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.", model="m", clock=_CLK, user_id="owner",
        mood_enabled=False, closeness_enabled=False,
        thoughts_enabled=True, thought_tools_enabled=master, thought_prompt=thought_prompt,
        embedder=MockEmbedder(), recall_enabled=True, embed_model="m@x", recall_tool_enabled=recall_tool,
        file_tool_enabled=file_tool, files_dir=tmp_path / "files",
    )


# --- %recall (memory, inward, trusted) ------------------------------------------------------------
def test_recall_runs_the_recall_tool_in_the_think_path(tmp_path):
    mock = MockLLMClient("Згадалося, як ми говорили про море.\nЕМОЦІЯ: tender",
                         tool_script=[("recall", {"query": "море"})])
    core = _core(tmp_path, mock)
    out = core.run_directive("%recall", core.start_session())
    assert out.is_directive and out.thought.kind == "recall"
    assert [c[0] for c in mock.tool_calls] == ["recall"]   # the recall tool ran (inward)


def test_recall_absent_when_recall_tool_off(tmp_path):
    mock = MockLLMClient("x\nЕМОЦІЯ: calm", tool_script=[("recall", {"query": "x"})])
    assert _core(tmp_path, mock, recall_tool=False).run_directive(
        "%recall", _core(tmp_path, mock, recall_tool=False).start_session()).is_directive is False


# --- %prompt (open, owner-only, the topic IS the instruction) -------------------------------------
def test_prompt_runs_for_owner_as_a_self_directed_act(tmp_path):
    mock = MockLLMClient("Зробила, як ти просив.\nЕМОЦІЯ: calm")
    core = _core(tmp_path, mock)
    out = core.run_directive("%prompt подивись, що нового", core.start_session(), is_owner=True)
    assert out.is_directive and out.thought.kind == "prompt" and out.thought is not None


def test_prompt_is_owner_only(tmp_path):
    mock = MockLLMClient("x\nЕМОЦІЯ: calm")
    core = _core(tmp_path, mock)
    out = core.run_directive("%prompt зроби щось", core.start_session(), is_owner=False)
    assert out.is_directive is False   # a non-owner can't run %prompt → plain chat


def test_prompt_absent_when_flag_off(tmp_path):
    mock = MockLLMClient("x\nЕМОЦІЯ: calm")
    assert _core(tmp_path, mock, thought_prompt=False).run_directive(
        "%prompt x", _core(tmp_path, mock, thought_prompt=False).start_session()).is_directive is False

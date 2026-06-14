"""v0.19 LUMI-083 — safety bounds: per-turn read cap, loop-cap termination, never-raise (no model)."""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.files import FileTools
from core.llm import MockLLMClient
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 6, 15, 12, 0, tzinfo=UTC))


# --- per-turn read budget (FileTools level) --------------------------------------------------------
def _big(tmp_path, n=20):
    (tmp_path / "big.txt").write_text("\n".join(f"L{i}" for i in range(1, n + 1)) + "\n", encoding="utf-8")


def test_per_turn_read_total_cap_blocks_further_reads(tmp_path):
    _big(tmp_path)
    ft = FileTools(tmp_path, read_lines=10, read_max_total=5)
    out1 = ft.execute("read_file", {"path": "big.txt", "start_line": 1, "line_count": 10})
    assert "1: L1" in out1 and "5: L5" in out1 and "6: L6" not in out1  # shrunk to the budget (5)
    out2 = ft.execute("read_file", {"path": "big.txt", "start_line": 6, "line_count": 10})
    assert "read limit reached" in out2  # budget exhausted → notice, not more lines


def test_read_shrinks_to_remaining_budget(tmp_path):
    _big(tmp_path, 10)
    ft = FileTools(tmp_path, read_lines=10, read_max_total=7)
    ft.execute("read_file", {"path": "big.txt", "start_line": 1, "line_count": 4})  # reads 4
    out = ft.execute("read_file", {"path": "big.txt", "start_line": 5, "line_count": 10})  # 3 remain
    assert "5: L5" in out and "7: L7" in out and "8: L8" not in out


# --- Core-level bounds & degradation ---------------------------------------------------------------
def _sandbox(tmp_path, user, name, text):
    root = tmp_path / "files" / user
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text, encoding="utf-8")


def _core(tmp_path, llm, *, read_max_total=2000, max_steps=8, user="owner") -> Core:
    return Core(
        llm=llm, repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m", clock=_CLK,
        user_id=user, mood_enabled=False, closeness_enabled=False, thoughts_enabled=False,
        file_tool_enabled=True, files_dir=tmp_path / "files",
        file_read_lines=10, file_read_max_total=read_max_total, file_find_max=5, tool_max_steps=max_steps,
    )


def test_core_loop_cap_terminates_with_valid_reply(tmp_path):
    _sandbox(tmp_path, "owner", "f.txt", "a\nb\nc\n")
    mock = MockLLMClient(states={"reply": "досить", "emotion": "calm", "intensity": 0.5},
                         tool_script=[("read_file", {"path": "f.txt"})] * 10)
    core = _core(tmp_path, mock, max_steps=2)
    state = core.reply("читай все", core.start_session())
    assert state.reply == "досить" and state.emotion.value == "calm"  # terminated despite a 10-step script
    assert len(mock.tool_calls) == 2  # capped at max_steps


def test_core_turn_completes_despite_tool_errors(tmp_path):
    mock = MockLLMClient(states={"reply": "не знайшла", "emotion": "thoughtful", "intensity": 0.4},
                         tool_script=[("read_file", {"path": "../escape"}),
                                      ("read_file", {"path": "missing.md"})])
    core = _core(tmp_path, mock)
    state = core.reply("read", core.start_session())
    assert state.emotion.value == "thoughtful"  # the turn completed, never raised
    assert "traversal" in mock.tool_calls[0][2] and "file not found" in mock.tool_calls[1][2]


def test_per_turn_budget_resets_between_turns(tmp_path):
    _sandbox(tmp_path, "owner", "big.txt", "\n".join(f"L{i}" for i in range(1, 21)) + "\n")
    mock = MockLLMClient(states={"reply": "ок", "emotion": "calm", "intensity": 0.5},
                         tool_script=[("read_file", {"path": "big.txt", "start_line": 1, "line_count": 10})])
    core = _core(tmp_path, mock, read_max_total=5)
    core.reply("turn1", core.start_session())
    assert "5: L5" in mock.tool_calls[-1][2] and "6: L6" not in mock.tool_calls[-1][2]  # capped this turn
    core.reply("turn2", core.start_session())  # a fresh turn → fresh FileTools → fresh budget
    assert "5: L5" in mock.tool_calls[-1][2]  # can read again

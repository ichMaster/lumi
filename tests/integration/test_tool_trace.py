"""v0.19 tool trace — Core records the file tools used each turn (for the TUI) + a tool-log.jsonl."""
from __future__ import annotations

import json
from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 6, 16, 12, 0, tzinfo=UTC))


def _core(tmp_path, *, trace: bool):
    root = tmp_path / "files" / "owner"
    root.mkdir(parents=True, exist_ok=True)
    (root / "notes.md").write_text("вступ\nРозділ 4: оплата\nкінець\n", encoding="utf-8")
    return Core(
        llm=MockLLMClient(states={"reply": "ок", "emotion": "calm", "intensity": 0.5},
                          tool_script=[("find_in_file", {"path": "notes.md", "query": "Розділ 4"}),
                                       ("read_file", {"path": "notes.md", "start_line": 2, "line_count": 1})]),
        repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m", clock=_CLK,
        mood_enabled=False, closeness_enabled=False, thoughts_enabled=False,
        file_tool_enabled=True, files_dir=tmp_path / "files", file_read_lines=10,
        file_tool_trace=trace, tool_log_path=(tmp_path / "tool-log.jsonl") if trace else None,
    )


def test_trace_on_records_calls_and_writes_log(tmp_path):
    core = _core(tmp_path, trace=True)
    core.reply("прочитай розділ про оплату", core.start_session())

    # in-memory trace (the TUI reads this)
    assert [c[0] for c in core.last_tool_calls] == ["find_in_file", "read_file"]
    assert "Розділ 4" in core.last_tool_calls[0][2]              # find result
    assert "Розділ 4: оплата" in core.last_tool_calls[1][2]      # read result

    # the streamed log
    log = (tmp_path / "tool-log.jsonl").read_text(encoding="utf-8").splitlines()
    recs = [json.loads(line) for line in log]
    assert [r["kind"] for r in recs] == ["find_in_file", "read_file"]
    assert recs[0]["input"] == {"path": "notes.md", "query": "Розділ 4"}
    assert len(recs[1]["result"]) <= 200                         # result truncated in the log


def test_trace_off_records_nothing(tmp_path):
    core = _core(tmp_path, trace=False)
    core.reply("прочитай", core.start_session())
    assert core.last_tool_calls == []
    assert not (tmp_path / "tool-log.jsonl").exists()


def test_trace_resets_per_turn(tmp_path):
    core = _core(tmp_path, trace=True)
    core.reply("раз", core.start_session())
    assert len(core.last_tool_calls) == 2
    core.reply("два", core.start_session())  # fresh turn → fresh list, not appended
    assert len(core.last_tool_calls) == 2


def test_trace_wraps_the_v0_20_write_tools(tmp_path):
    (tmp_path / "files" / "owner").mkdir(parents=True, exist_ok=True)
    core = Core(
        llm=MockLLMClient(states={"reply": "ок", "emotion": "calm", "intensity": 0.5},
                          tool_script=[("create_file", {"path": "todo.md", "content": "пункт 1\n"}),
                                       ("append_file", {"path": "todo.md", "content": "пункт 2\n"})]),
        repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m", clock=_CLK,
        mood_enabled=False, closeness_enabled=False, thoughts_enabled=False,
        file_tool_enabled=True, files_dir=tmp_path / "files",
        file_tool_trace=True, tool_log_path=tmp_path / "tool-log.jsonl",
    )
    core.reply("створи todo і додай пункт", core.start_session())
    assert [c[0] for c in core.last_tool_calls] == ["create_file", "append_file"]  # write tools traced
    assert core.last_tool_calls[0][2].startswith("created todo.md")
    recs = [json.loads(line) for line in (tmp_path / "tool-log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["kind"] for r in recs] == ["create_file", "append_file"]

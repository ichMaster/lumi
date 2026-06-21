"""v0.33 — the directive output sink: `%name[!] [>sink]` saves the thought (code-owned, sandboxed).

Default → thoughts; `!` → also chat; `>notes` → notes/<date>.md; `>path` → a file; `>folder/` → a dated
file in that folder. Mock model — no paid calls; per-user sandbox; non-destructive.
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 6, 21, 9, 30, tzinfo=UTC))


def _core(tmp_path, text="думка для збереження", *, file_tool=True, thought_tools=False, user="owner"):
    return Core(
        llm=MockLLMClient(f"{text}\nЕМОЦІЯ: calm"), repository=JsonRepository(tmp_path / "store.json"),
        canon="C", model="m", clock=_CLK, user_id=user, mood_enabled=False, closeness_enabled=False,
        thoughts_enabled=True, thought_tools_enabled=thought_tools,
        file_tool_enabled=file_tool, files_dir=tmp_path / "files",
    )


def test_sink_notes_saves_to_dated_notes_file(tmp_path):
    core = _core(tmp_path)
    out = core.run_directive("%wonder >notes", core.start_session())
    assert out.is_directive and out.saved_to == "notes/2026-06-21.md"
    body = (tmp_path / "files" / "owner" / "notes" / "2026-06-21.md").read_text(encoding="utf-8")
    assert "думка для збереження" in body and "09:30" in body


def test_sink_specific_file(tmp_path):
    core = _core(tmp_path)
    out = core.run_directive("%wonder >ideas/list.md", core.start_session())
    assert out.saved_to == "ideas/list.md"
    assert "думка для збереження" in (
        tmp_path / "files" / "owner" / "ideas" / "list.md").read_text(encoding="utf-8")


def test_sink_folder_gets_a_dated_file(tmp_path):
    core = _core(tmp_path)
    out = core.run_directive("%wonder >memos/", core.start_session())
    assert out.saved_to == "memos/2026-06-21.md"
    assert (tmp_path / "files" / "owner" / "memos" / "2026-06-21.md").exists()


def test_sink_is_sandboxed_and_does_not_falsely_confirm(tmp_path):
    core = _core(tmp_path)
    out = core.run_directive("%wonder >../escape.md", core.start_session())
    assert out.is_directive and out.thought is not None  # the thought is still recorded
    assert out.saved_to is None                          # the escaping write is refused → no false confirm
    assert not (tmp_path / "files" / "escape.md").exists() and not (tmp_path / "escape.md").exists()


def test_default_to_thoughts_no_sink(tmp_path):
    core = _core(tmp_path)
    out = core.run_directive("%wonder", core.start_session())  # no sink → thoughts only
    assert out.is_directive and out.saved_to is None and out.thought is not None


def test_sink_off_without_file_tool(tmp_path):
    core = _core(tmp_path, file_tool=False)
    out = core.run_directive("%wonder >notes", core.start_session())
    assert out.is_directive and out.saved_to is None and out.thought is not None  # no file tool → no save


def test_note_default_sink_still_saves_to_notes(tmp_path):
    core = _core(tmp_path, thought_tools=True)  # %note is a file-family directive (needs the master gate)
    out = core.run_directive("%note", core.start_session())  # %note has default_sink="notes"
    assert out.is_directive and out.saved_to == "notes/2026-06-21.md"


def test_open_and_sink_combine(tmp_path):
    core = _core(tmp_path)
    out = core.run_directive("%wonder! >notes", core.start_session())  # ! → chat, >notes → save
    assert out.mode == "open" and out.saved_to == "notes/2026-06-21.md"


def test_sink_is_per_user_isolated(tmp_path):
    a = _core(tmp_path, "секрет Аліси", user="alice")
    a.run_directive("%wonder >notes", a.start_session())
    assert (tmp_path / "files" / "alice" / "notes" / "2026-06-21.md").exists()
    assert not (tmp_path / "files" / "bob" / "notes" / "2026-06-21.md").exists()

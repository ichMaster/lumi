"""v0.33 LUMI-129 — file-thoughts (%note / %review / %explore / %journal) + per-family gating.

Off (master, family flag, or the tool/sandbox) → the directive is ABSENT (plain chat). Mock model — no
paid calls; per-user sandbox.
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 6, 21, 9, 30, tzinfo=UTC))


def _core(tmp_path, mock, *, thought_tools=True, file_tool=True, journal=False, thought_journal=False,
          user="owner"):
    return Core(
        llm=mock, repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.", model="m", clock=_CLK, user_id=user,
        mood_enabled=False, closeness_enabled=False,
        thoughts_enabled=True, thought_tools_enabled=thought_tools, thought_journal=thought_journal,
        file_tool_enabled=file_tool, files_dir=tmp_path / "files",
        journal_enabled=journal, journal_dir=tmp_path / "journal",
    )


def test_note_records_a_thought_and_code_appends_to_notes(tmp_path):
    mock = MockLLMClient("Сьогодні тихо й добре.\nЕМОЦІЯ: calm")
    core = _core(tmp_path, mock)
    out = core.run_directive("%note", core.start_session())
    assert out.is_directive and out.thought is not None and out.thought.kind == "note"
    notes = tmp_path / "files" / "owner" / "notes" / "2026-06-21.md"  # notes/, not journal/ (distinct from %journal)
    assert notes.exists()
    body = notes.read_text(encoding="utf-8")
    assert "Сьогодні тихо й добре." in body and "09:30" in body  # code-appended, stamped


def test_note_append_is_non_destructive(tmp_path):
    core = _core(tmp_path, MockLLMClient("перша.\nЕМОЦІЯ: calm"))
    core.run_directive("%note", core.start_session())
    core2 = _core(tmp_path, MockLLMClient("друга.\nЕМОЦІЯ: calm"))
    core2.run_directive("%note", core2.start_session())
    body = (tmp_path / "files" / "owner" / "notes" / "2026-06-21.md").read_text(encoding="utf-8")
    assert "перша." in body and "друга." in body  # appended, not overwritten


def test_review_runs_the_file_tool_loop(tmp_path):
    root = tmp_path / "files" / "owner"
    root.mkdir(parents=True)
    (root / "notes.md").write_text("давня нотатка\n", encoding="utf-8")
    mock = MockLLMClient("Перечитала свої записи.\nЕМОЦІЯ: tender",
                         tool_script=[("read_file", {"path": "notes.md", "start_line": 1})])
    core = _core(tmp_path, mock)
    out = core.run_directive("%review", core.start_session())
    assert out.is_directive and out.thought.kind == "review"
    assert [c[0] for c in mock.tool_calls] == ["read_file"]   # read in the think loop


def test_file_thoughts_absent_when_file_tool_off(tmp_path):
    core = _core(tmp_path, MockLLMClient("x\nЕМОЦІЯ: calm"), file_tool=False)  # file off
    for d in ("%note", "%review", "%explore"):
        assert core.run_directive(d, core.start_session()).is_directive is False  # absent → plain chat


def test_file_thoughts_absent_when_master_off(tmp_path):
    core = _core(tmp_path, MockLLMClient("x\nЕМОЦІЯ: calm"), thought_tools=False)  # master gate off
    assert core.run_directive("%review", core.start_session()).is_directive is False


def test_journal_directive_gated_by_thought_journal_flag(tmp_path):
    # journal tool on but the per-family flag OFF → %journal absent
    off = _core(tmp_path, MockLLMClient("x\nЕМОЦІЯ: calm"), journal=True, thought_journal=False)
    assert off.run_directive("%journal", off.start_session()).is_directive is False
    # both on → %journal fires (runs the journal tool loop)
    on = _core(tmp_path, MockLLMClient("Підсумувала день.\nЕМОЦІЯ: thoughtful",
                                       tool_script=[("journal_write", {"text": "день був теплий"})]),
               journal=True, thought_journal=True)
    out = on.run_directive("%journal", on.start_session())
    assert out.is_directive and out.thought.kind == "journal"
    assert [c[0] for c in on._llm.tool_calls] == ["journal_write"]


def test_note_is_per_user_isolated(tmp_path):
    a = _core(tmp_path, MockLLMClient("секрет Аліси.\nЕМОЦІЯ: calm"), user="alice")
    a.run_directive("%note", a.start_session())
    # alice's note lives only under her sandbox; bob's sandbox has none
    assert (tmp_path / "files" / "alice" / "notes" / "2026-06-21.md").exists()
    assert not (tmp_path / "files" / "bob" / "notes" / "2026-06-21.md").exists()

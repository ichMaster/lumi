"""v0.19 LUMI-080 — the sandboxed read executor + 3 read tools (core/files.py). No model."""
from __future__ import annotations

import os

from core.files import READ_TOOL_NAMES, READ_TOOLS, FileTools


def _root(tmp_path):
    (tmp_path / "notes.md").write_text(
        "\n".join(f"line {i}" for i in range(1, 51)) + "\nРозділ 4: оплата\n" + "tail\n",
        encoding="utf-8",
    )
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "inner.txt").write_text("hello\nworld\n", encoding="utf-8")
    return FileTools(tmp_path, read_lines=10, find_max=3)


# --- tool defs -------------------------------------------------------------------------------------
def test_read_tools_shape():
    assert READ_TOOL_NAMES == {"list_files", "find_in_file", "read_file"}
    for t in READ_TOOLS:
        assert {"name", "description", "input_schema"} <= t.keys()


# --- list_files ------------------------------------------------------------------------------------
def test_list_files_lists_root_and_subdir(tmp_path):
    ft = _root(tmp_path)
    out = ft.execute("list_files", {})
    assert "notes.md" in out and "sub/" in out and "bytes" in out
    assert "inner.txt" in ft.execute("list_files", {"path": "sub"})


# --- find_in_file ----------------------------------------------------------------------------------
def test_find_in_file_returns_line_numbers(tmp_path):
    ft = _root(tmp_path)
    out = ft.execute("find_in_file", {"path": "notes.md", "query": "Розділ 4"})
    assert "line 51:" in out and "оплата" in out


def test_find_in_file_caps_matches(tmp_path):
    ft = _root(tmp_path)  # find_max=3; "line " appears on 50 lines
    out = ft.execute("find_in_file", {"path": "notes.md", "query": "line "})
    match_lines = [ln for ln in out.splitlines() if ln.strip().startswith("line ")]
    assert len(match_lines) == 3 and "capped at 3" in out  # 3 matches + the cap notice


def test_find_in_file_no_match_and_missing_query(tmp_path):
    ft = _root(tmp_path)
    assert "No matches" in ft.execute("find_in_file", {"path": "notes.md", "query": "zzz"})
    assert "missing 'query'" in ft.execute("find_in_file", {"path": "notes.md"})


# --- read_file -------------------------------------------------------------------------------------
def test_read_file_window_and_total_lines(tmp_path):
    ft = _root(tmp_path)
    out = ft.execute("read_file", {"path": "notes.md", "start_line": 5, "line_count": 3})
    assert "total_lines=52" in out
    assert "5: line 5" in out and "7: line 7" in out and "8: line 8" not in out


def test_read_file_per_call_line_cap(tmp_path):
    ft = _root(tmp_path)  # read_lines=10
    out = ft.execute("read_file", {"path": "notes.md", "start_line": 1, "line_count": 999})
    assert "1: line 1" in out and "10: line 10" in out and "11: line 11" not in out


def test_read_file_past_end(tmp_path):
    ft = _root(tmp_path)
    assert "past the end" in ft.execute("read_file", {"path": "notes.md", "start_line": 999})


# --- sandbox + graceful degradation ---------------------------------------------------------------
def test_sandbox_rejects_traversal_absolute_and_returns_error_string(tmp_path):
    ft = _root(tmp_path)
    assert "traversal" in ft.execute("read_file", {"path": "../secret.txt"})
    assert "absolute path not allowed" in ft.execute("read_file", {"path": "/etc/passwd"})
    assert "missing 'path'" in ft.execute("read_file", {})


def test_sandbox_rejects_symlink_escape(tmp_path):
    outside = tmp_path.parent / "outside_secret.txt"
    outside.write_text("TOP SECRET\n", encoding="utf-8")
    ft = _root(tmp_path)
    os.symlink(outside, tmp_path / "link.txt")
    out = ft.execute("read_file", {"path": "link.txt"})
    assert "escapes the sandbox" in out and "TOP SECRET" not in out


def test_executor_never_raises(tmp_path):
    ft = _root(tmp_path)
    assert ft.execute("read_file", {"path": "nope.md"}).startswith("error:")
    assert ft.execute("bogus_tool", {"path": "x"}).startswith("error: unknown file tool")
    assert ft.execute("read_file", {"path": "notes.md", "start_line": "x"}).startswith("error:")

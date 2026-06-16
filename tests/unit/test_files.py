"""v0.19 LUMI-080 — the sandboxed read executor + 3 read tools (core/files.py). No model.

v0.20 LUMI-085 — the two non-destructive write tools (create_file / append_file) extend the same
executor (the "--- write tools ---" section below).
"""
from __future__ import annotations

import os

from core.files import (
    READ_TOOL_NAMES,
    READ_TOOLS,
    WRITE_TOOL_NAMES,
    WRITE_TOOLS,
    FileTools,
)


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


# --- write tools (v0.20 LUMI-085) -----------------------------------------------------------------
def test_write_tools_shape():
    assert WRITE_TOOL_NAMES == {"create_file", "append_file"}
    for t in WRITE_TOOLS:
        assert {"name", "description", "input_schema"} <= t.keys()
        assert t["input_schema"]["required"] == ["path", "content"]


def test_create_file_writes_a_new_file(tmp_path):
    ft = FileTools(tmp_path)
    out = ft.execute("create_file", {"path": "note.md", "content": "привіт\nсвіт\n"})
    assert out.startswith("created note.md")
    assert (tmp_path / "note.md").read_text(encoding="utf-8") == "привіт\nсвіт\n"


def test_create_file_makes_parent_dirs_under_root(tmp_path):
    ft = FileTools(tmp_path)
    assert ft.execute("create_file", {"path": "sub/deep/n.md", "content": "x"}).startswith("created")
    assert (tmp_path / "sub" / "deep" / "n.md").read_text(encoding="utf-8") == "x"


def test_create_file_refuses_existing_and_leaves_it_intact(tmp_path):
    (tmp_path / "keep.md").write_text("ORIGINAL\n", encoding="utf-8")
    ft = FileTools(tmp_path)
    out = ft.execute("create_file", {"path": "keep.md", "content": "CLOBBER"})
    assert "already exists" in out and "no overwrite" in out
    assert (tmp_path / "keep.md").read_text(encoding="utf-8") == "ORIGINAL\n"  # untouched


def test_append_file_appends_to_the_end(tmp_path):
    (tmp_path / "log.md").write_text("рядок1\n", encoding="utf-8")
    ft = FileTools(tmp_path)
    out = ft.execute("append_file", {"path": "log.md", "content": "рядок2\n"})
    assert out.startswith("appended")
    assert (tmp_path / "log.md").read_text(encoding="utf-8") == "рядок1\nрядок2\n"  # order preserved


def test_append_file_refuses_missing_file(tmp_path):
    ft = FileTools(tmp_path)
    out = ft.execute("append_file", {"path": "ghost.md", "content": "x"})
    assert "file not found" in out and "does not create" in out
    assert not (tmp_path / "ghost.md").exists()  # no implicit create


def test_write_size_cap_refuses_oversize(tmp_path):
    ft = FileTools(tmp_path, write_max=8)
    out = ft.execute("create_file", {"path": "big.md", "content": "x" * 9})
    assert "too large" in out
    assert not (tmp_path / "big.md").exists()  # refused before any write


def test_write_missing_content(tmp_path):
    ft = FileTools(tmp_path)
    assert "missing 'content'" in ft.execute("create_file", {"path": "a.md"})
    assert "missing 'content'" in ft.execute("append_file", {"path": "a.md", "content": 123})


def test_write_tools_are_sandboxed(tmp_path):
    ft = FileTools(tmp_path)
    assert "traversal" in ft.execute("create_file", {"path": "../escape.md", "content": "x"})
    assert "absolute path" in ft.execute("create_file", {"path": "/tmp/escape.md", "content": "x"})
    assert not (tmp_path.parent / "escape.md").exists()  # nothing written outside the root


def test_no_overwrite_or_delete_tool_exists(tmp_path):
    # The executor knows only the read + the two non-destructive write tools — nothing destructive.
    ft = FileTools(tmp_path)
    for destructive in ("write_file", "overwrite_file", "delete_file", "rm"):
        assert ft.execute(destructive, {"path": "x", "content": "y"}).startswith("error: unknown file tool")

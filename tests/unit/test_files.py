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
    _fmt_ts,
    _stat_dates,
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
    assert READ_TOOL_NAMES == {"list_files", "find_in_file", "read_file", "stat_file", "search_files"}
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


# --- search_files (v0.32) -------------------------------------------------------------------------
def _search_root(tmp_path):
    (tmp_path / "a.md").write_text("vino\nкава тут\nбільше тексту\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.md").write_text("intro\nкава і там\n", encoding="utf-8")
    return FileTools(tmp_path, search_max_files=50, search_max_lines=20, search_max_chars=4000)


def test_search_files_finds_across_files_with_line_numbers(tmp_path):
    ft = _search_root(tmp_path)
    out = ft.execute("search_files", {"query": "кава"})
    assert "a.md:2:" in out and "sub/b.md:2:" in out          # path:line across two files
    assert "кава тут" in out and "кава і там" in out


def test_search_files_no_match_and_missing_query(tmp_path):
    ft = _search_root(tmp_path)
    assert "No matches" in ft.execute("search_files", {"query": "zzz"})
    assert "missing 'query'" in ft.execute("search_files", {})


def test_search_files_path_narrows_to_subfolder(tmp_path):
    ft = _search_root(tmp_path)
    out = ft.execute("search_files", {"query": "кава", "path": "sub"})
    assert "sub/b.md:2:" in out and "a.md:" not in out        # only the subfolder scanned


def test_search_files_regex(tmp_path):
    ft = _search_root(tmp_path)
    out = ft.execute("search_files", {"query": "тут$", "regex": True})
    assert "a.md:2:" in out and "sub/b.md" not in out         # 'тут$' matches only the line ending in 'тут'
    assert "bad regex" in ft.execute("search_files", {"query": "[", "regex": True})


def test_search_files_caps_lines(tmp_path):
    (tmp_path / "many.md").write_text("\n".join("hit here" for _ in range(20)) + "\n", encoding="utf-8")
    ft = FileTools(tmp_path, search_max_lines=3)
    out = ft.execute("search_files", {"query": "hit"})
    hits = [ln for ln in out.splitlines() if "many.md:" in ln]
    assert len(hits) == 3 and "capped" in out


def test_search_files_skips_binary(tmp_path):
    (tmp_path / "text.md").write_text("findme here\n", encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01findme\xff\xfe")  # invalid UTF-8 → skipped
    ft = FileTools(tmp_path)
    out = ft.execute("search_files", {"query": "findme"})
    assert "text.md:1:" in out and "blob.bin" not in out      # binary skipped, no crash


def test_search_files_skips_oversize(tmp_path):
    (tmp_path / "small.md").write_text("needle\n", encoding="utf-8")
    (tmp_path / "huge.md").write_text("needle\n" + "x" * 100, encoding="utf-8")
    ft = FileTools(tmp_path, copy_max=20)  # the per-file scan ceiling reuses copy_max
    out = ft.execute("search_files", {"query": "needle"})
    assert "small.md:1:" in out and "huge.md" not in out      # oversize skipped


def test_search_files_sandboxed(tmp_path):
    ft = _search_root(tmp_path)
    assert "traversal" in ft.execute("search_files", {"query": "x", "path": ".."})


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
    assert WRITE_TOOL_NAMES == {"create_file", "append_file", "create_folder", "copy_file"}
    for t in WRITE_TOOLS:
        assert {"name", "description", "input_schema"} <= t.keys()
    required = {t["name"]: t["input_schema"].get("required", []) for t in WRITE_TOOLS}
    assert required["create_file"] == ["path", "content"]
    assert required["append_file"] == ["path", "content"]
    assert required["create_folder"] == ["path"]          # v0.29
    assert required["copy_file"] == ["src", "dest"]        # v0.29


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
    # The executor knows only the read + the non-destructive write/filesystem tools — nothing destructive.
    ft = FileTools(tmp_path)
    for destructive in ("write_file", "overwrite_file", "delete_file", "rm", "move_file", "rename_file"):
        assert ft.execute(destructive, {"path": "x", "content": "y"}).startswith("error: unknown file tool")


# --- v0.29 metadata + filesystem tools (dates / stat_file / create_folder / copy_file) ------------
def test_list_files_shows_dates(tmp_path):
    ft = _root(tmp_path)
    out = ft.execute("list_files", {})
    assert "created" in out and "modified" in out  # dates alongside the size


def test_stat_file_reports_size_and_dates(tmp_path):
    ft = _root(tmp_path)
    out = ft.execute("stat_file", {"path": "notes.md"})
    assert out.startswith("notes.md:") and "bytes" in out and "created" in out and "modified" in out


def test_stat_file_missing_and_not_a_file(tmp_path):
    ft = _root(tmp_path)
    assert "file not found" in ft.execute("stat_file", {"path": "nope.md"})
    assert "not a file" in ft.execute("stat_file", {"path": "sub"})  # a directory


def test_stat_dates_created_falls_back_to_ctime():
    # Platform-independent: where st_birthtime is absent, created uses st_ctime; where present, it wins.
    class _NoBirth:
        st_mtime = 1_700_000_000.0
        st_ctime = 1_690_000_000.0

    class _WithBirth(_NoBirth):
        st_birthtime = 1_680_000_000.0

    assert _stat_dates(_NoBirth()) == (_fmt_ts(1_690_000_000.0), _fmt_ts(1_700_000_000.0))
    assert _stat_dates(_WithBirth()) == (_fmt_ts(1_680_000_000.0), _fmt_ts(1_700_000_000.0))


def test_create_folder_creates_and_refuses_existing(tmp_path):
    ft = FileTools(tmp_path)
    assert ft.execute("create_folder", {"path": "diary"}).startswith("created folder")
    assert (tmp_path / "diary").is_dir()
    out = ft.execute("create_folder", {"path": "diary"})  # second time
    assert "already exists" in out and "no overwrite" in out


def test_create_folder_makes_parents_and_is_sandboxed(tmp_path):
    ft = FileTools(tmp_path)
    assert ft.execute("create_folder", {"path": "a/b/c"}).startswith("created folder")
    assert (tmp_path / "a" / "b" / "c").is_dir()
    assert "traversal" in ft.execute("create_folder", {"path": "../escape"})
    assert not (tmp_path.parent / "escape").exists()  # nothing created outside the root


def test_copy_file_copies_to_new_dest(tmp_path):
    (tmp_path / "a.txt").write_text("payload\n", encoding="utf-8")
    ft = FileTools(tmp_path)
    out = ft.execute("copy_file", {"src": "a.txt", "dest": "b.txt"})
    assert out.startswith("copied")
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "payload\n"
    assert (tmp_path / "a.txt").exists()  # source kept (copy, not move)


def test_copy_file_refuses_existing_dest(tmp_path):
    (tmp_path / "a.txt").write_text("SRC\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("ORIGINAL\n", encoding="utf-8")
    ft = FileTools(tmp_path)
    out = ft.execute("copy_file", {"src": "a.txt", "dest": "b.txt"})
    assert "already exists" in out and "no overwrite" in out
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "ORIGINAL\n"  # untouched (no overwrite)


def test_copy_file_refuses_missing_and_non_file_source(tmp_path):
    (tmp_path / "sub").mkdir()
    ft = FileTools(tmp_path)
    assert "source not found" in ft.execute("copy_file", {"src": "ghost.txt", "dest": "x.txt"})
    assert "not a file" in ft.execute("copy_file", {"src": "sub", "dest": "x.txt"})  # a directory source


def test_copy_file_size_cap_refuses_oversize(tmp_path):
    (tmp_path / "big.bin").write_bytes(b"x" * 100)
    ft = FileTools(tmp_path, copy_max=50)
    out = ft.execute("copy_file", {"src": "big.bin", "dest": "copy.bin"})
    assert "too large" in out
    assert not (tmp_path / "copy.bin").exists()  # refused before the copy


def test_copy_file_sandboxes_both_paths(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    ft = FileTools(tmp_path)
    assert "traversal" in ft.execute("copy_file", {"src": "../a.txt", "dest": "b.txt"})
    assert "traversal" in ft.execute("copy_file", {"src": "a.txt", "dest": "../b.txt"})
    assert "absolute path" in ft.execute("copy_file", {"src": "a.txt", "dest": "/tmp/escape.txt"})
    assert not (tmp_path.parent / "b.txt").exists()  # nothing copied outside the root

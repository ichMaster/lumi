"""v0.28 LUMI-110 — the JournalTools executor + journal_write/read/list (core/journal.py).

An injected stamp + a temp sandbox — no model, no network. The executor stamps the code-owned header, is
non-destructive (create-then-append), reads/lists by date, and never raises.
"""
from __future__ import annotations

from core.journal import (
    JOURNAL_TOOL_NAMES,
    JOURNAL_TOOLS,
    JournalTools,
)

_STAMP = "> **Настрій:** тонка шкіра сьогодні\n> **Біоритми:** емоційний −0.95 (low)\n> **Прогноз:** відплив"


def _tools(root, *, date="2026-06-17", time="21:30", stamp=_STAMP, max_chars=4000):
    return JournalTools(root, date=date, time=time, stamp=stamp, max_chars=max_chars)


# --- tool def --------------------------------------------------------------------------------------
def test_journal_tools_shape():
    assert JOURNAL_TOOL_NAMES == {"journal_write", "journal_read", "journal_list"}
    names = {t["name"] for t in JOURNAL_TOOLS}
    assert names == {"journal_write", "journal_read", "journal_list"} and all("." not in n for n in names)
    write = next(t for t in JOURNAL_TOOLS if t["name"] == "journal_write")
    assert write["input_schema"]["required"] == ["text"]  # only `text` — metadata is NOT a tool arg


# --- write: code-owned header + create, then append (non-destructive) ------------------------------
def test_write_creates_with_code_owned_header(tmp_path):
    out = _tools(tmp_path).execute("journal_write", {"text": "Весь день був з-під води."})
    assert "створено запис 2026-06-17" in out
    body = (tmp_path / "journal" / "2026-06-17.md").read_text(encoding="utf-8")
    assert body.startswith("# 2026-06-17\n\n")
    assert "**Настрій:** тонка шкіра" in body and "**Біоритми:**" in body and "**Прогноз:** відплив" in body
    assert "Весь день був з-під води." in body


def test_second_write_same_day_appends(tmp_path):
    tools = _tools(tmp_path)
    tools.execute("journal_write", {"text": "Перша частина."})
    out = tools.execute("journal_write", {"text": "Друга частина, пізніше."})
    assert "додано до запису 2026-06-17 (## 21:30)" in out
    body = (tmp_path / "journal" / "2026-06-17.md").read_text(encoding="utf-8")
    assert "Перша частина." in body and "Друга частина, пізніше." in body  # first survives (non-destructive)
    assert "## 21:30" in body
    assert body.count("# 2026-06-17") == 1 and body.count("**Настрій:**") == 1  # header stamped once


def test_body_is_capped(tmp_path):
    _tools(tmp_path, max_chars=50).execute("journal_write", {"text": "я" * 9000})
    body = (tmp_path / "journal" / "2026-06-17.md").read_text(encoding="utf-8")
    prose = body.split("\n\n", 2)[2]  # after "# date" + stamp
    assert prose.rstrip("\n").endswith("…") and len(prose) <= 60


def test_write_missing_text(tmp_path):
    assert "missing 'text'" in _tools(tmp_path).execute("journal_write", {})


# --- read + list by date ---------------------------------------------------------------------------
def test_read_by_date(tmp_path):
    tools = _tools(tmp_path)
    tools.execute("journal_write", {"text": "запис дня"})
    out = tools.execute("journal_read", {"date": "2026-06-17"})
    assert "# 2026-06-17" in out and "запис дня" in out


def test_read_default_is_most_recent(tmp_path):
    _tools(tmp_path, date="2026-06-15").execute("journal_write", {"text": "старіший"})
    _tools(tmp_path, date="2026-06-17").execute("journal_write", {"text": "новіший"})
    # a reader anchored at a day with no entry → falls back to the most recent
    out = _tools(tmp_path, date="2026-06-20").execute("journal_read", {})
    assert "новіший" in out and "старіший" not in out


def test_read_missing_date_errors(tmp_path):
    assert "не знайдено" in _tools(tmp_path).execute("journal_read", {"date": "2000-01-01"})


def test_read_empty_journal(tmp_path):
    assert "ще немає записів" in _tools(tmp_path).execute("journal_read", {})


def test_list_newest_first(tmp_path):
    for d in ("2026-06-15", "2026-06-17", "2026-06-16"):
        _tools(tmp_path, date=d).execute("journal_write", {"text": f"day {d}"})
    out = _tools(tmp_path).execute("journal_list", {})
    assert out.index("2026-06-17") < out.index("2026-06-16") < out.index("2026-06-15")


def test_list_empty(tmp_path):
    assert "ще немає записів" in _tools(tmp_path).execute("journal_list", {})


# --- safety: traversal refused, unknown tool, never raises -----------------------------------------
def test_read_traversal_refused(tmp_path):
    out = _tools(tmp_path).execute("journal_read", {"date": "../../etc/passwd"})
    assert out.startswith("error:")  # safe_path rejects, degraded to a string (never raises)


def test_unknown_tool(tmp_path):
    assert _tools(tmp_path).execute("bogus", {}).startswith("error: unknown journal tool")

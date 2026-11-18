"""v4.3 LUMI-309 — the journal's read side for the window (``core/journal.py``: ``parse_entry``, ``list_days``,
``read_day``; ``core/user.py`` ``may_read_journal``; ``Core.journal_root``).

Pinned on the shapes found on prod (synthetic text, no real entry): a full stamp; a stamp without the forecast; a day
written before the stamp existed (a non-date heading, no timed section, its own headings); prose before the first
timed section; the days newest first; the refusals — and that nothing in the journal folder ever changes."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from core.browse import BadPath, NotFound, TooLarge
from core.journal import JOURNAL_MAX_DAY, is_day, list_days, parse_biorhythms, parse_entry, read_day
from core.user import may_read_journal

FULL = """# 2026-10-21

> **Настрій:** Тихий настрій, хочеться писати.
> **Біоритми:** фізичний +0.52 (rising) · емоційний +1.00 (high) · інтелектуальний -0.31 (falling)
> **Прогноз:** День для повільних думок.

## 00:30

Перший запис дня.

Другий абзац.

## 15:16

Пізніше — ще одна думка.
"""

NO_FORECAST = """# 2026-06-16

> **Настрій:** Зосереджена.
> **Біоритми:** фізичний −0.63 (low) · емоційний +0.10 (critical) · інтелектуальний +0.70 (high)

## 03:57

Ніч.
"""

BEFORE_STAMP = """# 17 червня

Сьогодні був дивний день.

### Що я помітила

- одне
- друге

## Підсумок

Кінець.
"""


def _fingerprint(root: Path) -> list[tuple]:
    rows = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in sorted(dirnames + filenames):
            p = Path(dirpath) / name
            st = p.lstat()
            digest = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() and not p.is_symlink() else ""
            rows.append((str(p.relative_to(root)), st.st_size, st.st_mtime_ns, digest))
    return sorted(rows)


def _journal(tmp_path: Path) -> Path:
    root = tmp_path / "journal" / "owner"
    root.mkdir(parents=True)
    (root / "2026-10-21.md").write_text(FULL, encoding="utf-8")
    (root / "2026-06-16.md").write_text(NO_FORECAST, encoding="utf-8")
    (root / "2026-06-17.md").write_text(BEFORE_STAMP, encoding="utf-8")
    return root


# --- parse_entry -------------------------------------------------------------------------------------------------
def test_a_full_stamp_and_its_timed_sections():
    day = parse_entry("2026-10-21", FULL)
    assert day["date"] == "2026-10-21"
    assert day["stamp"] == {
        "mood": "Тихий настрій, хочеться писати.",
        "biorhythms": [{"name": "фізичний", "value": 0.52, "label": "rising"},
                       {"name": "емоційний", "value": 1.0, "label": "high"},
                       {"name": "інтелектуальний", "value": -0.31, "label": "falling"}],
        "forecast": "День для повільних думок.",
    }
    assert day["sections"] == [{"time": "00:30", "text": "Перший запис дня.\n\nДругий абзац."},
                               {"time": "15:16", "text": "Пізніше — ще одна думка."}]
    assert day["markdown"] == FULL


def test_a_stamp_without_the_forecast_and_a_unicode_minus():
    day = parse_entry("2026-06-16", NO_FORECAST)
    assert day["stamp"]["forecast"] is None
    assert day["stamp"]["mood"] == "Зосереджена."
    assert day["stamp"]["biorhythms"][0] == {"name": "фізичний", "value": -0.63, "label": "low"}
    assert [s["time"] for s in day["sections"]] == ["03:57"]


def test_a_day_before_the_stamp_is_one_untimed_section_with_its_own_headings():
    day = parse_entry("2026-06-17", BEFORE_STAMP)
    assert day["stamp"] is None
    assert len(day["sections"]) == 1
    section = day["sections"][0]
    assert section["time"] is None
    assert section["text"].startswith("Сьогодні був дивний день.")
    assert "### Що я помітила" in section["text"] and "## Підсумок" in section["text"]  # not a timed section


def test_prose_before_the_first_timed_section_comes_first_untimed():
    text = "# 2026-07-01\n\n> **Настрій:** Тихо.\n\nБез часу.\n\n## 09:15\n\nЗ часом.\n"
    day = parse_entry("2026-07-01", text)
    assert day["sections"] == [{"time": None, "text": "Без часу."}, {"time": "09:15", "text": "З часом."}]


def test_another_blockquote_key_stays_in_the_prose_and_a_bad_biorhythm_line_is_null():
    text = ("# 2026-07-02\n\n> **Настрій:** Тихо.\n> **Тема:** небо\n> **Біоритми:** якось так\n\n"
            "## 10:00\n\nТекст.\n")
    day = parse_entry("2026-07-02", text)
    assert day["stamp"] == {"mood": "Тихо.", "biorhythms": None, "forecast": None}
    assert day["sections"][0] == {"time": None, "text": "> **Тема:** небо"}


def test_a_heading_only_day_and_an_empty_one_have_no_sections():
    assert parse_entry("2026-07-03", "# 2026-07-03\n")["sections"] == []
    assert parse_entry("2026-07-03", "") == {"date": "2026-07-03", "stamp": None, "sections": [], "markdown": ""}


def test_windows_line_ends_and_an_empty_timed_section():
    day = parse_entry("2026-07-04", "# 2026-07-04\r\n\r\n## 08:00\r\n\r\n## 09:00\r\n\r\nТак.\r\n")
    assert day["sections"] == [{"time": "08:00", "text": ""}, {"time": "09:00", "text": "Так."}]


def test_parse_biorhythms_needs_every_cell():
    assert parse_biorhythms("фізичний +0.10 (rising)") == [{"name": "фізичний", "value": 0.1, "label": "rising"}]
    assert parse_biorhythms("фізичний +0.10 (rising) · ще щось") is None
    assert parse_biorhythms("") is None


# --- list_days / read_day ----------------------------------------------------------------------------------------
def test_the_days_newest_first_with_their_sections_and_stamp(tmp_path):
    root = _journal(tmp_path)
    (root / "notes.md").write_text("не день", encoding="utf-8")
    (root / "2026-02-30.md").write_text("неможлива дата", encoding="utf-8")
    (root / "2026-11-01.md").mkdir()  # a folder with a day's name
    before = _fingerprint(root)
    days = list_days(root)
    assert days == {"truncated": False, "days": [
        {"date": "2026-10-21", "sections": 2, "size": len(FULL.encode()), "stamp": True},
        {"date": "2026-06-17", "sections": 1, "size": len(BEFORE_STAMP.encode()), "stamp": False},
        {"date": "2026-06-16", "sections": 1, "size": len(NO_FORECAST.encode()), "stamp": True},
    ]}
    assert _fingerprint(root) == before


def test_a_link_out_of_the_journal_is_never_listed_or_read(tmp_path):
    root = _journal(tmp_path)
    outside = tmp_path / "secret.md"
    outside.write_text("# not hers\n", encoding="utf-8")
    (root / "2026-12-01.md").symlink_to(outside)
    assert [d["date"] for d in list_days(root)["days"]] == ["2026-10-21", "2026-06-17", "2026-06-16"]
    with pytest.raises(BadPath):
        read_day(root, "2026-12-01")


def test_a_fifo_is_never_listed_or_opened(tmp_path):
    root = _journal(tmp_path)
    os.mkfifo(root / "2026-12-02.md")
    assert "2026-12-02" not in [d["date"] for d in list_days(root)["days"]]
    with pytest.raises(NotFound):
        read_day(root, "2026-12-02")


def test_the_listing_is_capped(tmp_path, monkeypatch):
    import core.journal as journal

    root = _journal(tmp_path)
    monkeypatch.setattr(journal, "JOURNAL_MAX_DAYS", 2)
    days = list_days(root)
    assert [d["date"] for d in days["days"]] == ["2026-10-21", "2026-06-17"]
    assert days["truncated"] is True


def test_a_journal_that_does_not_exist_lists_empty(tmp_path):
    assert list_days(tmp_path / "journal" / "owner") == {"days": [], "truncated": False}
    assert not (tmp_path / "journal").exists()  # a read creates nothing


def test_read_day_parses_the_day(tmp_path):
    root = _journal(tmp_path)
    before = _fingerprint(root)
    assert read_day(root, "2026-10-21") == parse_entry("2026-10-21", FULL)
    assert read_day(root, "2026-06-17")["stamp"] is None
    assert _fingerprint(root) == before


@pytest.mark.parametrize("bad", ["2026-02-30", "2026-13-01", "26-10-21", "2026-10-21.md", "../2026-10-21",
                                 "/etc/passwd", "", None, 20261021])
def test_read_day_refuses_what_is_not_a_date(tmp_path, bad):
    root = _journal(tmp_path)
    with pytest.raises(BadPath):
        read_day(root, bad)


def test_read_day_missing_and_too_large(tmp_path):
    root = _journal(tmp_path)
    with pytest.raises(NotFound):
        read_day(root, "2026-10-22")
    (root / "2026-10-23.md").write_bytes(b"x" * (JOURNAL_MAX_DAY + 1))
    with pytest.raises(TooLarge):
        read_day(root, "2026-10-23")
    assert [d["date"] for d in list_days(root)["days"]][0] == "2026-10-23"  # still listed, by its first 256 KB


def test_a_broken_byte_reads_as_a_replacement_character(tmp_path):
    root = _journal(tmp_path)
    (root / "2026-10-24.md").write_bytes("# 2026-10-24\n\n## 10:00\n\nтак".encode() + b"\xff\n")
    assert read_day(root, "2026-10-24")["sections"][0]["text"] == "так�"


def test_is_day():
    assert is_day("2026-11-18")
    assert not is_day("2026-11-31")
    assert not is_day("2026-1-18")
    assert not is_day(None)


# --- who may read it, and where it is ---------------------------------------------------------------------------
def test_only_the_owner_may_read_the_journal():
    assert may_read_journal("owner") is True
    assert may_read_journal("ada") is False
    assert may_read_journal("") is False


def test_the_core_names_its_users_journal_root_without_creating_it(tmp_path):
    from core.agent import Core
    from core.llm import MockLLMClient
    from state.local_store import JsonRepository

    core = Core(llm=MockLLMClient(), repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m",
                user_id="ada", journal_dir=tmp_path / "journal")
    assert core.journal_root == tmp_path / "journal" / "ada"
    assert not (tmp_path / "journal").exists()

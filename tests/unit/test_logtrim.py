"""Log retention — trim_log_days keeps the last N days across the three log shapes, in place."""
from datetime import date

from core.logtrim import LOG_FILES, trim_log_days, trim_lumi_logs

_TODAY = date(2026, 7, 20)


def test_trims_leading_date_lines_keeps_continuations(tmp_path):
    p = tmp_path / "lumi.log"
    p.write_text(
        "2026-07-01 10:00:00 INFO old line\n"
        "  a traceback continuation of the old line\n"
        "2026-07-19 10:00:00 INFO recent line\n"
        "  its continuation\n",
        encoding="utf-8",
    )
    assert trim_log_days(p, days=10, today=_TODAY) is True
    kept = p.read_text(encoding="utf-8")
    assert "old line" not in kept and "traceback continuation" not in kept  # dropped with its record
    assert "recent line" in kept and "its continuation" in kept            # kept with its record


def test_trims_mood_block_markers(tmp_path):
    p = tmp_path / "mood.log"
    p.write_text(
        "===== 2026-07-01 =====\n# старий гороскоп\n"
        "===== 2026-07-18 =====\n# свіжий гороскоп\n",
        encoding="utf-8",
    )
    trim_log_days(p, days=10, today=_TODAY)
    kept = p.read_text(encoding="utf-8")
    assert "старий" not in kept and "свіжий" in kept


def test_trims_jsonl_by_ts_field(tmp_path):
    p = tmp_path / "cache-log.jsonl"
    p.write_text(
        '{"ts": "2026-07-02T00:24:21+03:00", "kind": "old"}\n'
        '{"ts": "2026-07-20T00:24:21+03:00", "kind": "new"}\n',
        encoding="utf-8",
    )
    trim_log_days(p, days=10, today=_TODAY)
    kept = p.read_text(encoding="utf-8")
    assert '"old"' not in kept and '"new"' in kept


def test_noop_when_all_within_window_preserves_inode(tmp_path):
    p = tmp_path / "lumi.log"
    p.write_text("2026-07-19 10:00:00 INFO recent\n", encoding="utf-8")
    ino_before = p.stat().st_ino
    assert trim_log_days(p, days=10, today=_TODAY) is False  # nothing older → didn't rewrite
    assert p.stat().st_ino == ino_before                     # untouched


def test_in_place_rewrite_keeps_same_inode(tmp_path):
    # The rewrite truncates the SAME inode (not a rename) so a concurrent O_APPEND writer stays valid.
    p = tmp_path / "lumi.log"
    p.write_text("2026-07-01 10:00:00 INFO old\n2026-07-19 10:00:00 INFO new\n", encoding="utf-8")
    ino_before = p.stat().st_ino
    assert trim_log_days(p, days=10, today=_TODAY) is True
    assert p.stat().st_ino == ino_before


def test_missing_file_is_a_noop(tmp_path):
    assert trim_log_days(tmp_path / "nope.log", days=10, today=_TODAY) is False


def test_trim_lumi_logs_covers_the_known_logs(tmp_path):
    for name in LOG_FILES:
        (tmp_path / name).write_text("2026-07-01 10:00:00 x\n2026-07-19 10:00:00 y\n", encoding="utf-8")
    assert trim_lumi_logs(tmp_path, days=10) >= 0  # returns a count, never raises

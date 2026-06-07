"""Unit tests for the idle-nudge logic (LUMI-022) — pure, clock-driven, no sleeps."""

from datetime import UTC, datetime, timedelta

from core.config import load_config
from core.nudge import load_nudges, should_nudge

_T0 = datetime(2026, 6, 7, 14, 0, tzinfo=UTC)


def test_should_nudge_fires_after_the_interval():
    assert should_nudge(_T0, _T0 + timedelta(seconds=240), 240) is True
    assert should_nudge(_T0, _T0 + timedelta(seconds=239), 240) is False


def test_quiet_hours_suppress_the_nudge():
    last = datetime(2026, 6, 7, 1, 0, tzinfo=UTC)
    night = datetime(2026, 6, 7, 2, 0, tzinfo=UTC)  # 02:00 — inside 23–7 (wraps)
    assert should_nudge(last, night, 0, quiet_hours=(23, 7)) is False
    day = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)  # outside quiet, and idle since 01:00
    assert should_nudge(last, day, 0, quiet_hours=(23, 7)) is True


def test_load_nudges_from_the_authored_file():
    nudges = load_nudges(load_config(load_env=False).nudge_path)
    assert "ти тут?" in nudges
    assert all(not n.startswith("#") for n in nudges)  # comments skipped


def test_load_nudges_missing_file_is_empty(tmp_path):
    assert load_nudges(tmp_path / "nope.md") == []

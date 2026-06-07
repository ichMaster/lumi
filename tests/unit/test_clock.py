"""Unit tests for the injectable clock + timestamp formatters (LUMI-019)."""

from datetime import UTC, datetime

from core.clock import fixed_clock, format_date, format_stamp, system_clock


def test_format_stamp_is_compact_and_deterministic():
    assert format_stamp("2026-06-07T14:30:00+00:00") == "2026-06-07 14:30"


def test_format_date_is_just_the_date():
    assert format_date("2026-06-07T14:30:00+00:00") == "2026-06-07"


def test_formatters_pass_through_bad_input():
    assert format_stamp("not-a-date") == "not-a-date"
    assert format_date(None) is None  # type: ignore[arg-type]


def test_fixed_clock_returns_the_given_time():
    when = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    assert fixed_clock(when)() == when


def test_system_clock_is_timezone_aware():
    assert system_clock().tzinfo is not None

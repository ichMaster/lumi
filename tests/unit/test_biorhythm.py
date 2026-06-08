"""Unit tests for the v0.8 biorhythm engine (LUMI-031) — exact, deterministic."""

import math
from datetime import date

import pytest

from core.biorhythm import (
    PERIODS,
    biorhythms,
    format_biorhythms,
    load_birth_date,
)
from core.config import load_config

BIRTH = date(2002, 3, 5)  # Лілі's natal birth date (core/natal.md)


def test_values_are_exact_sine_of_days_since_birth():
    today = date(2026, 6, 8)
    d = (today - BIRTH).days
    b = biorhythms(BIRTH, today)
    for cycle in b:
        period = PERIODS[cycle.name]
        assert cycle.value == pytest.approx(math.sin(2 * math.pi * d / period))


def test_deterministic_same_inputs_same_result():
    today = date(2026, 6, 8)
    assert biorhythms(BIRTH, today) == biorhythms(BIRTH, today)


def test_birth_day_is_all_zero_and_critical():
    b = biorhythms(BIRTH, BIRTH)  # d = 0 → sin(0) = 0 for every cycle
    for cycle in b:
        assert cycle.value == pytest.approx(0.0)
        assert cycle.label == "critical"


def test_high_label_at_a_physical_peak():
    # +6 days: sin(2π·6/23) ≈ 0.997 → high (not at a crossing).
    b = biorhythms(BIRTH, BIRTH.fromordinal(BIRTH.toordinal() + 6))
    assert b.physical.value > 0.7
    assert b.physical.label == "high"


def test_critical_label_at_a_physical_zero_crossing():
    # The physical cycle (23 d) crosses zero between day 11 and 12 → day 11 is critical.
    b = biorhythms(BIRTH, BIRTH.fromordinal(BIRTH.toordinal() + 11))
    assert b.physical.label == "critical"


def test_load_birth_date_from_the_authored_natal_file():
    assert load_birth_date(load_config(load_env=False).natal_path) == BIRTH


def test_load_birth_date_missing_or_garbled(tmp_path):
    assert load_birth_date(tmp_path / "nope.md") is None  # missing file
    bad = tmp_path / "natal.md"
    bad.write_text("Народження: невідомо, Львів.", encoding="utf-8")
    assert load_birth_date(bad) is None  # no parseable date
    bad.write_text("Народження: 32.13.2002, Львів.", encoding="utf-8")
    assert load_birth_date(bad) is None  # invalid date


def test_format_biorhythms_renders_all_three():
    text = format_biorhythms(biorhythms(BIRTH, date(2026, 6, 8)))
    for label in ("фізичний", "емоційний", "інтелектуальний"):
        assert label in text
    assert "(" in text and ")" in text  # each carries a phase label

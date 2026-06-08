"""Biorhythms — three deterministic sine cycles from Лілі's birth date (v0.8).

Unlike the v0.6 horoscope (model-written, can't compute transits), biorhythms are exact
math: ``sin(2π · days_since_birth / period)`` for the classic **physical (23 d)**,
**emotional (28 d)**, and **intellectual (33 d)** cycles. Pure and deterministic — the
caller passes ``today`` (from the injected clock) — so it's fully unit-testable with no
model and no wall-clock read. The birth date is parsed from ``core/natal.md`` (the same
source the horoscope uses). The result is **merged into the daily mood call** (LUMI-032)
and shown by ``/biorhythm`` (LUMI-033).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# Classic biorhythm periods, in days.
PERIODS: dict[str, int] = {"physical": 23, "emotional": 28, "intellectual": 33}

# Ukrainian labels for display (the order is fixed: physical → emotional → intellectual).
_UA = {"physical": "фізичний", "emotional": "емоційний", "intellectual": "інтелектуальний"}

# "Народження: DD.MM.YYYY, …" in the natal file → the birth date.
_BIRTH_RE = re.compile(r"Народження:\s*(\d{1,2})\.(\d{1,2})\.(\d{4})")


@dataclass(frozen=True)
class Cycle:
    """One biorhythm cycle: its value in −1…+1 and a phase label."""

    name: str  # physical | emotional | intellectual
    value: float  # sin(2π·d/period), −1…+1
    label: str  # high | low | rising | falling | critical


@dataclass(frozen=True)
class Biorhythms:
    """The three cycles for a given day."""

    physical: Cycle
    emotional: Cycle
    intellectual: Cycle

    def __iter__(self):
        return iter((self.physical, self.emotional, self.intellectual))


def _label(value: float, value_next: float) -> str:
    """Phase label: ``critical`` at/around a zero-crossing, else high/low/rising/falling."""
    if value == 0.0 or (value > 0) != (value_next > 0):
        return "critical"  # the cycle crosses zero today/tonight — the classic unstable day
    if value >= 0.7:
        return "high"
    if value <= -0.7:
        return "low"
    return "rising" if value_next > value else "falling"


def biorhythms(birth_date: date, today: date) -> Biorhythms:
    """The three cycles for ``today``, exact and deterministic from ``birth_date``."""
    d = (today - birth_date).days
    cycles: dict[str, Cycle] = {}
    for name, period in PERIODS.items():
        value = math.sin(2 * math.pi * d / period)
        value_next = math.sin(2 * math.pi * (d + 1) / period)
        cycles[name] = Cycle(name, value, _label(value, value_next))
    return Biorhythms(cycles["physical"], cycles["emotional"], cycles["intellectual"])


def parse_birth_date(natal_text: str) -> date | None:
    """Parse ``Народження: DD.MM.YYYY`` from the natal **text** → a ``date`` (else ``None``)."""
    match = _BIRTH_RE.search(natal_text or "")
    if not match:
        return None
    day, month, year = (int(g) for g in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def load_birth_date(natal_path: str | Path) -> date | None:
    """Read the natal **file** and parse the birth date → a ``date`` (else ``None``).

    ``None`` (missing file / no parseable line / invalid date) → biorhythms simply off,
    so the v0.6 mood runs horoscope-only.
    """
    p = Path(natal_path)
    if not p.is_file():
        return None
    return parse_birth_date(p.read_text(encoding="utf-8"))


def format_biorhythms(b: Biorhythms) -> str:
    """Compact one-line rendering, e.g. ``фізичний +0.82 (high) · емоційний −0.61 (low) · …``."""
    return " · ".join(f"{_UA[c.name]} {c.value:+.2f} ({c.label})" for c in b)

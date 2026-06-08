"""Hormonal (menstrual) cycle — a phase-based body rhythm that colors the mood (v0.8.x).

A companion to the sine **biorhythms**: where those are smooth high/low waves, this is a
**phased** ~28-day cycle (menstruation → follicular → ovulation → luteal → PMS). Like the
biorhythms it is **computed in code** from an authored anchor (a known "day 1" in
``core/natal.md``) + the injected clock, and **merged into the same daily mood call** — it
colors her **tone, energy and sensitivity, never her competence**. Part of Лілі's embodied
self-image (inner, like her inner life), not a medical claim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

DEFAULT_LENGTH = 28  # a typical cycle; the luteal phase is ~14 days, so ovulation ≈ L−14.

# Each phase's short note describes how it tends to color her temperament (tone/energy),
# never competence. Tasteful — mood/sensitivity, not clinical detail.
_NOTES: dict[str, str] = {
    "менструація": "тіло просить тиші й тепла; енергія низька, настрій тихий, ніжний, трохи втомлений",
    "фолікулярна": "енергія росте, легкість і відкритість, цікавість до нового",
    "овуляція": "пік: багато енергії й тепла, впевненість, тягне до людей і творчості",
    "лютеїнова": "темп сповільнюється, тягне в затишок і спокій, більше в собі",
    "ПМС": "усе відчувається гостріше: підвищена чутливість, дратівливість, сльозливість, втома",
}

# "Цикл: … DD.MM.YYYY …" anchor + optional "довжина NN" length, in the natal file.
# `.*?` (non-greedy) skips any label text before the date — incl. "день 1 —".
_ANCHOR_RE = re.compile(r"Цикл:.*?(\d{1,2})\.(\d{1,2})\.(\d{4})")
_LENGTH_RE = re.compile(r"довжина\s*(\d{1,2})")


@dataclass(frozen=True)
class CyclePhase:
    """The hormonal-cycle phase for a given day."""

    day: int  # day in the cycle, 1…length
    length: int
    phase: str  # менструація | фолікулярна | овуляція | лютеїнова | ПМС
    note: str  # how it tends to color her tone/energy


def menstrual_phase(anchor: date, today: date, length: int = DEFAULT_LENGTH) -> CyclePhase:
    """The cycle phase for ``today``, deterministic from the ``anchor`` (a past day 1)."""
    day = (today - anchor).days % length + 1  # 1…length, repeats forever
    ovulation = length - 14  # luteal phase is ~constant 14 days
    if day <= 5:
        phase = "менструація"
    elif day >= length - 4:
        phase = "ПМС"  # the last ~5 days
    elif ovulation - 1 <= day <= ovulation + 1:
        phase = "овуляція"
    elif day < ovulation:
        phase = "фолікулярна"
    else:
        phase = "лютеїнова"
    return CyclePhase(day, length, phase, _NOTES[phase])


def parse_cycle_anchor(natal_text: str) -> tuple[date, int] | None:
    """Parse ``Цикл: день 1 — DD.MM.YYYY, довжина NN`` → ``(anchor, length)`` (else ``None``).

    ``None`` (no line / bad date) → the cycle is simply off. Length defaults to 28 and is
    clamped to a sane 20…40.
    """
    match = _ANCHOR_RE.search(natal_text or "")
    if not match:
        return None
    day, month, year = (int(g) for g in match.groups())
    try:
        anchor = date(year, month, day)
    except ValueError:
        return None
    length_match = _LENGTH_RE.search(natal_text)
    length = int(length_match.group(1)) if length_match else DEFAULT_LENGTH
    if not (20 <= length <= 40):
        length = DEFAULT_LENGTH
    return anchor, length


def format_cycle(c: CyclePhase) -> str:
    """Compact rendering, e.g. ``ПМС (день 26/28) — усе відчувається гостріше…``."""
    return f"{c.phase} (день {c.day}/{c.length}) — {c.note}"

"""Idle nudge (v0.4) — a hidden self-started turn after a long silence.

Pure, clock-driven helpers (testable, **no sleeps**): :func:`should_nudge` decides
if it's time; :func:`load_nudges` reads the authored openers. The TUI runs the
chosen opener through the normal turn **as a hidden user message** — the line is
never displayed, only Лілі's reply — so she appears to speak first.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def load_nudges(path: str | Path) -> list[str]:
    """Read the authored openers (one per line; ``#`` lines are comments)."""
    p = Path(path)
    if not p.is_file():
        return []
    return [
        line.strip()
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _in_quiet_hours(now: datetime, quiet: tuple[int, int]) -> bool:
    start, end = quiet
    if start == end:
        return False
    hour = now.hour
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # wraps past midnight


def should_nudge(
    last_activity: datetime,
    now: datetime,
    interval_s: int,
    quiet_hours: tuple[int, int] | None = None,
) -> bool:
    """True if it's been ``interval_s`` since ``last_activity`` (and not quiet hours)."""
    if quiet_hours and _in_quiet_hours(now, quiet_hours):
        return False
    return (now - last_activity).total_seconds() >= interval_s

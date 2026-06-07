"""A minimal injectable clock + timestamp formatters.

The clock is a callable returning an aware ``datetime`` — the **system clock** by
default, a **fixed clock** in tests. It drives the date-time stamps in prompts
(v0.4), the ambient "now" block, and the idle timer; the v0.6 mood reuses it. The
formatters render a stored ISO ``ts`` into compact strings **deterministically**
(no wall-clock reads, no timezone conversion) so prompts are testable.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

# A clock is just a callable returning an aware datetime.
Clock = Callable[[], datetime]


def system_clock() -> datetime:
    """The real clock: the current time, timezone-aware (UTC)."""
    return datetime.now(UTC)


def fixed_clock(when: datetime) -> Clock:
    """A clock stuck at ``when`` — for deterministic tests."""
    return lambda: when


def format_stamp(ts_iso: str) -> str:
    """Compact ``YYYY-MM-DD HH:MM`` from an ISO timestamp (the stored ``ts``)."""
    try:
        return datetime.fromisoformat(ts_iso).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return ts_iso


def format_date(ts_iso: str) -> str:
    """Just the date ``YYYY-MM-DD`` from an ISO timestamp."""
    try:
        return datetime.fromisoformat(ts_iso).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return ts_iso

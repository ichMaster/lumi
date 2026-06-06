"""Memory helpers — windowing now; summarization + facts land in v0.2 too.

Pure, unit-testable functions the core composes. v0.2 starts with the rolling
window (history trimming); LUMI-009/010 add summary/fact assembly here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")


def trim_history(messages: Sequence[T], max_messages: int) -> list[T]:
    """Keep only the last ``max_messages`` items for the model context.

    The full history stays persisted (ARCHITECTURE §Sessions and history); only
    the **in-context** window is trimmed before each model call. ``max_messages``
    comes from ``config.memory_window`` (no hardcoded N). ``<= 0`` keeps nothing.
    """
    if max_messages <= 0:
        return []
    return list(messages[-max_messages:])

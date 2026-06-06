"""Memory helpers — windowing now; summarization + facts land in v0.2 too.

Pure, unit-testable functions the core composes. v0.2 starts with the rolling
window (history trimming); LUMI-009/010 add summary/fact assembly here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from core.repository import Message

T = TypeVar("T")

# How many recent summaries to recall into context at startup (LUMI-011).
RECENT_SUMMARIES = 5

# Instruction for end-of-session summarization (an internal memory note, not Лілі speaking).
SUMMARY_SYSTEM = (
    "Ти стискаєш діалог у короткий підсумок для памʼяті Лілі. "
    "2–3 речення від третьої особи: суть розмови й важливе про співрозмовника. "
    "Без вступів і звертань — лише підсумок."
)


def trim_history(messages: Sequence[T], max_messages: int) -> list[T]:
    """Keep only the last ``max_messages`` items for the model context.

    The full history stays persisted (ARCHITECTURE §Sessions and history); only
    the **in-context** window is trimmed before each model call. ``max_messages``
    comes from ``config.memory_window`` (no hardcoded N). ``<= 0`` keeps nothing.
    """
    if max_messages <= 0:
        return []
    return list(messages[-max_messages:])


def summary_request(messages: Sequence[Message]) -> tuple[str, list[dict[str, str]]]:
    """Build the (system, messages) for an end-of-session summarization call.

    The session transcript goes in as a single user message; the system line
    asks for a compact third-person gist. The model's reply is the summary text.
    """
    transcript = "\n".join(f"{m.role}: {m.text}" for m in messages)
    return SUMMARY_SYSTEM, [{"role": "user", "content": transcript}]

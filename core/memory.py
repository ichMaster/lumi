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

# Instruction for end-of-session summarization (an internal memory note, not Лілі
# speaking). The target length is appended per-session (summary_request), scaled
# to the session size — a longer conversation earns a fuller summary.
SUMMARY_SYSTEM = (
    "Ти стискаєш діалог у підсумок для памʼяті Лілі — від третьої особи, по суті: "
    "про що говорили й важливе про співрозмовника. Без вступів і звертань — лише підсумок."
)

# Summary length bounds (sentences), scaled by message count.
_SUMMARY_MIN_SENTENCES = 1
_SUMMARY_MAX_SENTENCES = 8


def summary_sentences(n_messages: int) -> int:
    """Target summary length (sentences), scaled to the session size.

    Roughly one sentence per ~3 messages, clamped to [1, 8] — a short exchange
    gets a one-liner, a long conversation a fuller paragraph.
    """
    target = (n_messages + 2) // 3
    return max(_SUMMARY_MIN_SENTENCES, min(_SUMMARY_MAX_SENTENCES, target))

# Instruction for end-of-session long-term fact extraction (one durable fact per line).
FACTS_SYSTEM = (
    "Виокрем стійкі, довготривалі факти про співрозмовника з діалогу — "
    "по одному факту на рядок, стисло (імʼя, уподобання, важливі обставини). "
    "Лише те, що варто памʼятати надовго. Якщо нічого вартого — поверни порожньо."
)

# Leading bullet/numbering characters to strip from a fact line.
_BULLET_CHARS = "-•*–—0123456789.) \t"


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
    asks for a third-person gist whose **length scales with the session size**
    (``summary_sentences``). The model's reply is the summary text.
    """
    transcript = "\n".join(f"{m.role}: {m.text}" for m in messages)
    target = summary_sentences(len(messages))
    system = f"{SUMMARY_SYSTEM} Орієнтовний обсяг: {target} речень."
    return system, [{"role": "user", "content": transcript}]


def facts_request(messages: Sequence[Message]) -> tuple[str, list[dict[str, str]]]:
    """Build the (system, messages) for end-of-session long-term fact extraction."""
    transcript = "\n".join(f"{m.role}: {m.text}" for m in messages)
    return FACTS_SYSTEM, [{"role": "user", "content": transcript}]


def parse_facts(text: str) -> list[str]:
    """Parse the model's line-per-fact reply into clean fact strings.

    Strips bullets/numbering and drops blank lines; order preserved, no dedup
    (the core dedups against what's already stored).
    """
    facts: list[str] = []
    for line in text.splitlines():
        cleaned = line.strip().lstrip(_BULLET_CHARS).strip()
        if cleaned:
            facts.append(cleaned)
    return facts

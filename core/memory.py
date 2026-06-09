"""Memory helpers — windowing now; summarization + facts land in v0.2 too.

Pure, unit-testable functions the core composes. v0.2 starts with the rolling
window (history trimming); LUMI-009/010 add summary/fact assembly here.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from core.repository import Message

T = TypeVar("T")

# Short-memory recall (v0.9). N = last conversations injected in DETAIL; D = the day window
# whose conversations are injected as one-line GISTS (no row cap — bounded by the window).
RECENT_SUMMARIES = 5  # N
GIST_DAYS = 5  # D — the "days at a glance" window (local days)

# Instruction for end-of-session summarization (an internal memory note, not Лілі
# speaking). The target length is appended per-session (summary_request), scaled
# to the session size — a longer conversation earns a fuller summary.
SUMMARY_SYSTEM = (
    "Ти стискаєш діалог у підсумок для памʼяті Лілі — від третьої особи, по суті: "
    "про що говорили й важливе про співрозмовника. Без вступів і звертань. "
    "СПЕРШУ — детальний підсумок (орієнтовний обсяг нижче). А В КІНЦІ окремим рядком "
    "«СТИСЛО:» — одне коротке речення-суть (до ~15 слів) для швидкого пригадування."
)

# Pulls the trailing one-line gist ("СТИСЛО: …") off the summary reply.
_GIST_RE = re.compile(r"(?im)^[ \t]*стисло[ \t]*:[ \t]*(.+?)[ \t]*$")

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

# Instruction for in-session compaction: maintain a running digest of the earlier
# part of the *current* conversation (the messages that fell out of the verbatim
# window). Folds an existing digest together with a new chunk of older messages.
COMPACTION_DIGEST_SYSTEM = (
    "Ти ведеш стислий конспект ранньої частини поточної розмови — для контексту. "
    "Онови наявний конспект, додавши нові, давніші репліки. Від третьої особи, по суті, "
    "без вступів. Зберігай важливе (теми, рішення, факти), відкидай дрібниці."
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
    asks for a third-person gist whose **length scales with the session size**
    (``summary_sentences``). The model's reply is the summary text.
    """
    transcript = "\n".join(f"{m.role}: {m.text}" for m in messages)
    target = summary_sentences(len(messages))
    system = f"{SUMMARY_SYSTEM} Орієнтовний обсяг детальної частини: {target} речень."
    return system, [{"role": "user", "content": transcript}]


def parse_summary(text: str) -> tuple[str, str]:
    """Split a summary reply into ``(detailed, gist)`` — the v0.9 two tiers.

    The trailing «СТИСЛО: …» line is the **gist**; everything before it is the
    **detailed** summary. When the model omits the gist, fall back to the first
    sentence of the detailed summary (so a gist always exists).
    """
    text = text.strip()
    match = _GIST_RE.search(text)
    if match:
        gist = match.group(1).strip()
        detailed = text[: match.start()].strip()
    else:
        gist, detailed = "", text
    if not detailed:
        detailed = text
    if not gist:
        first = re.split(r"(?<=[.!?])\s", detailed, maxsplit=1)[0].strip()
        gist = first if len(first) <= 120 else detailed[:120].rstrip() + "…"
    return detailed, gist


def facts_request(messages: Sequence[Message]) -> tuple[str, list[dict[str, str]]]:
    """Build the (system, messages) for end-of-session long-term fact extraction."""
    transcript = "\n".join(f"{m.role}: {m.text}" for m in messages)
    return FACTS_SYSTEM, [{"role": "user", "content": transcript}]


def compaction_plan(n_messages: int, compacted_count: int, window: int, batch: int) -> int:
    """Decide how many oldest messages should be compacted (the new high-water mark).

    Floating window: keep the verbatim tail between ``window`` and
    ``window + batch``. When it would exceed ``window + batch``, fold the oldest
    messages down to a ``window``-length tail. Returns the new ``compacted_count``
    (>= the current one); equal means "nothing to compact this turn".
    """
    live = n_messages - compacted_count
    if live >= window + batch:
        return n_messages - window
    return compacted_count


def digest_request(
    existing: str | None, chunk: Sequence[Message]
) -> tuple[str, list[dict[str, str]]]:
    """Build the (system, messages) to fold ``chunk`` into the running digest."""
    transcript = "\n".join(f"{m.role}: {m.text}" for m in chunk)
    if existing:
        content = f"Наявний конспект:\n{existing}\n\nДавніші репліки, які треба додати:\n{transcript}"
    else:
        content = f"Давніші репліки розмови:\n{transcript}"
    return COMPACTION_DIGEST_SYSTEM, [{"role": "user", "content": content}]


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

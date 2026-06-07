"""Canon loading and system-prompt assembly.

The canon (``core/canon/lili.md``) is authored, static character content loaded
**verbatim** as the base of the system prompt (CANON_SPEC §1). The core never
hardcodes character content — it all lives in the canon file.

``build_system_prompt`` is the deliberate **extension seam**: in v0.1 it returns
the canon as-is; later versions assemble more *around* it (memory summaries +
facts in v0.2, the emotion-output instruction in v0.3, the daily mood block in
v0.5) without the core's callers changing.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

# Framing that makes the answer-style overlay a prioritized directive. Placed at
# the very end of the system prompt (last thing the model reads before the turn).
STYLE_HEADER = (
    "ВАЖЛИВО — ФОРМАТ І ДОВЖИНА ТВОЄЇ ВІДПОВІДІ. Дотримуйся цього СУВОРО; "
    "це має пріоритет над типовою багатослівністю та іншими вказівками щодо форми:"
)


def load_canon(path: str | Path) -> str:
    """Read the canon file. The path comes from config (never hardcoded).

    Raises a clear :class:`FileNotFoundError` if the canon is missing — Лілі's
    character must be present, never silently empty.
    """
    canon_path = Path(path)
    if not canon_path.is_file():
        raise FileNotFoundError(f"Canon file not found at {canon_path!s}")
    text = canon_path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Canon file at {canon_path!s} is empty")
    return text


def build_system_prompt(
    canon: str,
    summaries: Sequence[str] | None = None,
    facts: Sequence[str] | None = None,
    digest: str | None = None,
    style: str | None = None,
) -> str:
    """Assemble the system prompt: canon + the user's memory + an answer style.

    The canon always rides at the base, then the user's recent ``summaries`` and
    long-term ``facts``, then the in-session ``digest``, and finally — at the very
    **end** — an optional ``style`` overlay (which shapes the *form* of the reply:
    length/structure/expressiveness, never competence), framed as a prioritized
    directive (:data:`STYLE_HEADER`) so it's the last, most salient instruction.
    Assembly order: canon → summaries → facts → digest → **style**. With no
    overlays the result is the canon verbatim (the v0.1 behavior). v0.5 adds a
    ``mood`` block the same way.

    All overlay args are plain strings so this stays a pure string assembler,
    decoupled from the record types (the core passes the text).
    """
    parts = [canon]
    if summaries:
        parts.append(
            "Памʼять про попередні розмови з цією людиною:\n"
            + "\n".join(f"- {s}" for s in summaries)
        )
    if facts:
        parts.append(
            "Що ти памʼятаєш про цю людину:\n" + "\n".join(f"- {f}" for f in facts)
        )
    if digest:
        parts.append("Раніше в цій розмові (стисло):\n" + digest)
    if style:
        parts.append(f"{STYLE_HEADER}\n{style}")
    return "\n\n".join(parts)

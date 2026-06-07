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

import re
from collections.abc import Sequence
from pathlib import Path

# Keep the model's *reasoning* out of the visible reply, parseably. Opus 4.8 will
# otherwise narrate its planning into the answer text ("думаю. Це гра слів…"); a
# bare "don't reason out loud" instruction doesn't hold. Instead we ask it to wrap
# any pre-answer reasoning in <think>…</think> — which Claude follows reliably —
# and `split_reasoning` strips those tags out (the reasoning goes to the Thinking
# box; only what's outside the tags is the reply).
REASONING_DIRECTIVE = (
    "Якщо перед відповіддю ти міркуєш — загорни ці міркування на самому початку "
    "у теги <think>…</think>. Поза тегами лишай лише те, що ти кажеш співрозмовнику: "
    "без планів, службових нотаток чи пояснень власних намірів."
)

# Matches a well-formed <think>…</think> block (any reasoning the model wrapped).
_THINK_RE = re.compile(r"<think\b[^>]*>(.*?)</think\s*>", re.IGNORECASE | re.DOTALL)
# Catches any stray, one-sided <think>/</think> tag so it never shows in the reply.
_STRAY_THINK_RE = re.compile(r"</?think\b[^>]*>", re.IGNORECASE)


def split_reasoning(text: str) -> tuple[str | None, str]:
    """Split a model reply into ``(thinking, reply)``.

    Reasoning the model wrapped in ``<think>…</think>`` is extracted (joined if
    several) as ``thinking``; the ``reply`` is the text with those blocks — and any
    stray tags — removed and stripped. No tags → ``(None, text.strip())``.
    """
    thoughts = [m.strip() for m in _THINK_RE.findall(text)]
    thinking = "\n".join(t for t in thoughts if t) or None
    reply = _STRAY_THINK_RE.sub("", _THINK_RE.sub("", text)).strip()
    return thinking, reply

# Framing that makes the answer-style overlay a prioritized directive. Placed at
# the very end of the system prompt (last thing the model reads before the turn).
STYLE_HEADER = (
    "ВАЖЛИВО — ФОРМАТ І ДОВЖИНА ТВОЄЇ ВІДПОВІДІ. Дотримуйся цього СУВОРО; "
    "це має пріоритет над типовою багатослівністю та іншими вказівками щодо форми:"
)

# v0.3 emotion channel (EMOTION.md §3/§8). The `set_state` tool is the primary path,
# but it can't be *forced* while extended thinking is on — so Лілі also tags her
# state inline as <emotion>name intensity</emotion>, which `split_emotion` parses and
# strips. Either way every turn carries an emotion. Injected only when
# build_system_prompt(emotion=True).
EMOTION_INSTRUCTION = (
    "Наприкінці кожної відповіді додавай свій емоційний стан окремим тегом "
    "<emotion>назва інтенсивність</emotion> — назва: одне зі значень joy, calm, "
    "playful, tender, thoughtful, serious, surprise, doubt, sad; інтенсивність — "
    "число від 0 до 1 (напр. <emotion>joy 0.8</emotion>). Якщо доступний інструмент "
    "set_state — заповни його тими самими значеннями. Сам тег не коментуй."
)

# Inline emotion tag: <emotion>name</emotion> or <emotion>name 0.8</emotion>.
_EMOTION_TAG_RE = re.compile(
    r"<emotion>\s*([a-zA-Z]+)\s*([0-9]*\.?[0-9]+)?\s*</emotion>", re.IGNORECASE
)
_STRAY_EMOTION_RE = re.compile(r"</?emotion\b[^>]*>", re.IGNORECASE)


def split_emotion(text: str) -> tuple[dict | None, str]:
    """Extract an inline ``<emotion>name intensity</emotion>`` tag from a reply.

    Returns ``({emotion, intensity?}, clean_text)`` when a tag is present, else
    ``(None, text.strip())``. The tag — and any stray ``<emotion>`` markers — are
    removed so they never show in the reply. The fallback emotion channel for when
    the structured tool can't be forced (extended thinking on).
    """
    match = _EMOTION_TAG_RE.search(text)
    clean = _STRAY_EMOTION_RE.sub("", _EMOTION_TAG_RE.sub("", text)).strip()
    if not match:
        return None, clean
    emo: dict = {"emotion": match.group(1).lower()}
    if match.group(2):
        emo["intensity"] = float(match.group(2))
    return emo, clean


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
    emotion: bool = False,
    ambient: str | None = None,
) -> str:
    """Assemble the system prompt: canon + the user's memory + an answer style.

    The canon always rides at the base; ``emotion=True`` adds the v0.3
    emotion-output instruction (:data:`EMOTION_INSTRUCTION`) right after it; then
    the optional ``ambient`` "now / here" block (v0.4 — background that colors tone,
    never competence), then the user's recent ``summaries`` and long-term ``facts``,
    then the in-session ``digest``, and finally — at the very **end** — an optional ``style`` overlay
    (which shapes the *form* of the reply, never competence), framed as a
    prioritized directive (:data:`STYLE_HEADER`) so it's the last, most salient
    instruction. Assembly order: canon → emotion → summaries → facts → digest →
    **style**. With no overlays the result is the canon verbatim (the v0.1
    behavior). v0.5 adds a ``mood`` block the same way.

    All overlay args are plain strings so this stays a pure string assembler,
    decoupled from the record types (the core passes the text).
    """
    parts = [canon]
    if emotion:
        parts.append(EMOTION_INSTRUCTION)
    if ambient:
        parts.append(ambient)
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

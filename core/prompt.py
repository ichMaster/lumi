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

# Framing for the auto-style palette — placed at the very end of the system prompt.
# Лілі picks her own answer style (preferring mega/meta-styles), writes in it, and
# declares it as <style>name</style> (parsed + stripped by split_style). A `/style`
# recommendation, if set, rides inside the palette text as a soft bias.
STYLE_HEADER = (
    "ВАЖЛИВО — СТИЛЬ ВІДПОВІДІ. Обери з палітри МЕГА-стиль під момент і пиши в ньому. Це лише ФОРМА "
    "(не компетентність і не теплота). У кінці: <style>назва</style> (тег не коментуй)."
)

# Inline style tag: <style>лагідна</style> — Лілі's declared answer style for the turn.
_STYLE_TAG_RE = re.compile(r"<style>\s*([^<>]+?)\s*</style>", re.IGNORECASE)
_STRAY_STYLE_RE = re.compile(r"</?style\b[^>]*>", re.IGNORECASE)


def split_style(text: str) -> tuple[str | None, str]:
    """Extract Лілі's inline ``<style>name</style>`` choice from a reply.

    Returns ``(name, clean_text)`` (name lowercased/stripped) when present, else
    ``(None, text.strip())``. The tag and any stray ``<style>`` markers are removed
    so they never show in the reply — mirror of :func:`split_emotion`.
    """
    match = _STYLE_TAG_RE.search(text)
    clean = _STRAY_STYLE_RE.sub("", _STYLE_TAG_RE.sub("", text)).strip()
    if not match:
        return None, clean
    return match.group(1).strip().lower(), clean

# Framing for the v0.6 daily mood resolution — a prominent, prioritized block that
# colors her tone and the emotions she leans toward, never her competence.
MOOD_HEADER = (
    "ВАЖЛИВО — ТВІЙ НАСТРІЙ СЬОГОДНІ. Нехай він відчутно фарбує твій тон і емоції, "
    "до яких ти схиляєшся (емоцію в <emotion> теж). Це фон дня, не вказівка до дії; "
    "він НЕ змінює твоєї компетентності чи готовності допомогти:"
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

# v0.10: the per-turn relational read of the USER's message (feeds closeness). Internal.
RELATION_INSTRUCTION = (
    "Додатково оціни САМЕ ОСТАННЄ повідомлення співрозмовника (а не свою відповідь) і "
    "заповни поле relation інструмента set_state — кожен вимір число 0–1: warmth (тепло, "
    "турбота, прихильність), vulnerability (відкритість, довіра, ділиться чимось справжнім), "
    "playful (грайливість, гумор), harm (грубість, образа, жорстокість), manipulation (тиск, "
    "використання, обман). Це внутрішня оцінка — не згадуй і не коментуй її у відповіді."
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
    day_summaries: Sequence[str] | None = None,
    week_summaries: Sequence[str] | None = None,
    facts: Sequence[str] | None = None,
    digest: str | None = None,
    style: str | None = None,
    emotion: bool = False,
    relation: bool = False,
    ambient: str | None = None,
    mood: str | None = None,
    closeness: str | None = None,
    thoughts: str | None = None,
    recall: str | None = None,
) -> tuple[str, str]:
    """Assemble the system prompt, ordered for **prompt caching** (v0.15): a **stable prefix**
    then a **per-turn tail**. Returns ``(system, cache_prefix)`` where ``cache_prefix`` is the
    stable head and ``system.startswith(cache_prefix)`` always holds.

    - **Stable prefix** (cacheable — byte-identical within a session): the canon (persona) as
      prose, then `# Як відповідати` (emotion + relational read), `# Памʼять про цю людину`
      (the date-based memory layers coarse→fine + `## Факти`), and `# Настрій дня` (locked per local
      day). The in-session digest is **not** here — it changes on compaction (see the tail), so
      keeping it off the prefix means a compaction never re-writes the static head.
    - **Per-turn tail** (recomputed each turn → never cached): `# Раніше в цій розмові` (the
      in-session compaction — grows every `LUMI_COMPACTION_BATCH` messages), `# Релевантні моменти
      минулого` (the v0.17 per-turn RAG block — the query-relevant past, so it changes every turn), `# Зараз`
      (ambient now/here — its timestamp changes each turn), `# Близькість` (`update_closeness`
      rebuilds it each turn), `# Що в мене на думці` (the last-24h thoughts), and finally — kept
      **last + most salient** — `# Стиль відповіді` (the :data:`STYLE_HEADER`; *form*, never competence).

    With no overlays the result is the canon verbatim (the v0.1 behavior): ``(canon, canon)``.
    All overlay args are plain strings so this stays a pure string assembler.
    """
    # PREFIX — stable within a session (canon + instructions + memory + mood). Cacheable.
    prefix = [canon]

    fmt = []
    if emotion:
        fmt.append(EMOTION_INSTRUCTION)
    if relation:  # v0.10: the additive per-turn relational read of the user's message
        fmt.append(RELATION_INSTRUCTION)
    if fmt:
        prefix.append("# Як відповідати\n\n" + "\n\n".join(fmt))

    # Short memory grouped under one section, coarse → fine: weeks → days → recent sessions,
    # then long-term facts and the in-session digest.
    mem = []
    if week_summaries:
        mem.append("## Останні тижні\n" + "\n".join(f"- {w}" for w in week_summaries))
    if day_summaries:
        mem.append("## Останні дні\n" + "\n".join(f"- {d}" for d in day_summaries))
    if summaries:
        mem.append("## Останні розмови (детально)\n" + "\n".join(f"- {s}" for s in summaries))
    if facts:
        mem.append("## Факти\n" + "\n".join(f"- {f}" for f in facts))
    if mem:
        prefix.append("# Памʼять про цю людину\n\n" + "\n\n".join(mem))

    if mood:
        prefix.append("# Настрій дня\n\n" + f"{MOOD_HEADER}\n{mood}")

    # TAIL — recomputed each turn (digest, recall, ambient timestamp, closeness, thoughts); style last.
    tail = []
    if digest:  # the in-session compaction grows with the conversation (every LUMI_COMPACTION_BATCH
        # messages) — kept OFF the cached prefix so a compaction never re-writes the static head.
        tail.append("# Раніше в цій розмові\n\n" + digest)
    if recall:  # v0.17: the per-turn "relevant past moments" RAG block (query-relevant → never cached)
        tail.append("# Релевантні моменти минулого\n\n" + recall)
    if ambient:
        tail.append("# Зараз\n\n" + ambient)
    if closeness:  # v0.10: the active relationship level's block — rebuilt every turn
        tail.append("# Близькість\n\n" + closeness)
    if thoughts:  # v0.12: the last-24h dated diary — what's been on her mind (tone, not a report)
        tail.append("# Що в мене на думці (за останню добу)\n\n" + thoughts)
    if style:  # kept last — most salient (recency); shapes form, never competence
        tail.append("# Стиль відповіді\n\n" + f"{STYLE_HEADER}\n{style}")

    cache_prefix = "\n\n".join(prefix)
    system = "\n\n".join(prefix + tail)
    return system, cache_prefix


# A visible divider for the /prompt dump — shows where the cached prefix ends (v0.15).
CACHE_BREAKPOINT_MARKER = "━━━━━━ ✂ CACHE BREAKPOINT — cached prefix above · per-turn tail below ━━━━━━"


def mark_cache_breakpoint(system: str, cache_prefix: str | None) -> str:
    """Return ``system`` with a visible CACHE-BREAKPOINT divider at the ``cache_prefix`` boundary.

    **Display only** — used by the `/prompt` dump so the cache split is visible. The real prompt
    sent to the model is never modified (the marker is not part of ``system``). A ``None`` / whole /
    non-prefix ``cache_prefix`` returns ``system`` unchanged.
    """
    if cache_prefix and cache_prefix != system and system.startswith(cache_prefix):
        return f"{cache_prefix}\n\n{CACHE_BREAKPOINT_MARKER}{system[len(cache_prefix):]}"
    return system

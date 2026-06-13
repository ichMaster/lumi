"""Emoji rendering (v0.5) — the emotion channel shown as a glyph in the terminal.

A **renderer swap** over the locked v0.3 channel: no contract change. The
emotion→emoji+intensity table is an **editable authored file** (``LUMI_EMOJI_PATH``,
default ``core/emoji.md``); the built-in default below is the EMOTION.md §6 map.
``emoji_for(state)`` resolves emotion + ``intensity`` band → glyph(s); the resolved
map is **total over the enum** — a missing file / blank or unknown row / unknown
emotion falls back to the built-in default → the base glyph → ``calm``.

``intensity`` scales **emphasis, not the feeling**: the same face made stronger by
repeating it or adding an accent, across three bands (low/mid/high).
"""

from __future__ import annotations

from pathlib import Path

from core.emotion import DEFAULT_EMOTION, Emotion, EmotionState

# A row is the (low, mid, high) glyph(s) for one emotion.
EmojiRow = tuple[str, str, str]
EmojiMap = dict[Emotion, EmojiRow]

# Built-in default — EMOTION.md §6. Total over the enum; calm (neutral) does not escalate.
BUILTIN: EmojiMap = {
    Emotion.JOY: ("😄", "😄✨", "😄✨✨"),
    Emotion.CALM: ("🙂", "🙂", "🙂"),
    Emotion.PLAYFUL: ("😏", "😏😜", "😏😜😜"),
    Emotion.TENDER: ("🥰", "🥰💕", "🥰💕💕"),
    Emotion.THOUGHTFUL: ("🤔", "🤔💭", "🤔💭💭"),
    Emotion.SERIOUS: ("😐", "😐❗", "😐❗❗"),
    Emotion.SURPRISE: ("😮", "😮😮", "😮😮😮"),
    Emotion.DOUBT: ("😕", "😕❓", "😕❓❓"),
    Emotion.SAD: ("😢", "😢😢", "😢😢😢"),
}


def _band(intensity: float) -> int:
    """Intensity → band index: 0 low (<0.34), 1 mid (0.34–0.66), 2 high (≥0.67)."""
    if intensity < 0.34:
        return 0
    if intensity < 0.67:
        return 1
    return 2


def _row(parts: list[str]) -> EmojiRow:
    """A (low, mid, high) row from 1+ glyphs: one → all bands; else pad with the last."""
    low = parts[0]
    mid = parts[1] if len(parts) > 1 else low
    high = parts[2] if len(parts) > 2 else mid
    return (low, mid, high)


def load_emoji_map(path: str | Path) -> EmojiMap:
    """Load the authored emoji map, starting from :data:`BUILTIN` so it stays **total**.

    Format: ``emotion = low | mid | high`` (one glyph → all bands; ``#`` comments).
    A missing file, blank/unknown row, or unknown emotion name keeps the default.
    """
    resolved: EmojiMap = dict(BUILTIN)
    p = Path(path)
    if not p.is_file():
        return resolved
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        try:
            emotion = Emotion(name.strip().lower())
        except ValueError:
            continue  # unknown emotion name → skip (keep default)
        parts = [g.strip() for g in value.split("|") if g.strip()]
        if parts:  # a blank row keeps the default
            resolved[emotion] = _row(parts)
    return resolved


def emoji_for(state: EmotionState, emoji_map: EmojiMap | None = None) -> str:
    """Resolve an :class:`EmotionState` to its glyph(s) — **total over the enum**."""
    table = emoji_map or BUILTIN
    row = table.get(state.emotion) or BUILTIN.get(state.emotion) or BUILTIN[DEFAULT_EMOTION]
    return row[_band(state.intensity)]


class EmojiRenderer:
    """The v0.5 "emoji" render tier (EMOTION.md §5) — implements ``IEmotionRenderer``.

    ``render``/``glyph`` resolve ``emotion``(+``intensity``)→a glyph via the loaded
    map; ``set_speaking``/``tick`` are no-ops (like :class:`~core.emotion.LogRenderer`).
    ``render`` stores the glyph on ``last_glyph`` and, if given, calls ``sink(glyph)``.
    """

    def __init__(self, emoji_map: EmojiMap | None = None, sink=None) -> None:
        self._map = emoji_map or BUILTIN
        self._sink = sink
        self.last_glyph: str | None = None

    def glyph(self, state: EmotionState) -> str:
        return emoji_for(state, self._map)

    def render(self, state: EmotionState) -> None:
        self.last_glyph = self.glyph(state)
        if self._sink is not None:
            self._sink(self.last_glyph)

    def set_speaking(self, speaking: bool) -> None:
        pass  # v3.2+ voice → lip-sync; nothing to do in the emoji tier

    def tick(self, dt_ms: int) -> None:
        pass  # v4 idle loop; nothing to do in the emoji tier

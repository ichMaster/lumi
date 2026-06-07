"""The emotion channel contract — the locked ``EmotionState`` + 9-value enum.

Лілі's persona output is structured: every reply carries an ``emotion`` (from a
fixed set) and an ``intensity``. This shape is the contract **every render tier
reuses unchanged** — logged (v0.3), emoji (v0.4), local face (v0.6), web portrait
(v2.1), animation (v3). The model **emits** it; the core **validates** it
(LUMI-015); a **renderer** shows it (LUMI-017+). See
``specification/features/EMOTION.md`` §3–§5.

The contract, the enum, and the ``IEmotionRenderer`` interface are **locked in
v0.3 and never change** — only the renderer changes between versions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Emotion(StrEnum):
    """The fixed 9-value emotion set (EMOTION.md §4).

    ``StrEnum`` so members are their wire value (``Emotion.JOY == "joy"``) and
    JSON-serializable. ``calm`` is the neutral / fallback state (LUMI-015).
    """

    JOY = "joy"
    CALM = "calm"
    PLAYFUL = "playful"
    TENDER = "tender"
    THOUGHTFUL = "thoughtful"
    SERIOUS = "serious"
    SURPRISE = "surprise"
    DOUBT = "doubt"
    SAD = "sad"


# The neutral / fallback state, used when the model's emotion is unknown (LUMI-015).
DEFAULT_EMOTION = Emotion.CALM
# Default intensity when the model omits it (LUMI-015); the mid of the 0–1 range.
DEFAULT_INTENSITY = 0.5


@dataclass(frozen=True)
class EmotionState:
    """One model turn's output: Лілі's text + her state (EMOTION.md §3).

    The model emits exactly ``{reply, emotion, intensity}``. ``ttl_ms`` (relax to
    ``calm`` after idle, v3) and ``speaking`` (renderer-set during voice, v2.2) are
    **renderer-side** concerns — reserved in the spec, deliberately *not* in this
    shape, so the contract does not change when those tiers arrive.

    - ``reply`` — Лілі's text (required, non-empty after validation).
    - ``emotion`` — one of :class:`Emotion`.
    - ``intensity`` — float in ``[0.0, 1.0]`` (clamped by the validation gate).
    """

    reply: str
    emotion: Emotion
    intensity: float

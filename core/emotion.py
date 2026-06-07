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

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

# Repairs are logged here, keyed by session_id/turn (ARCHITECTURE §Observability).
_log = logging.getLogger("lumi.emotion")


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


class EmotionError(ValueError):
    """The model turn lacked a usable ``reply`` — surfaced to the interface, never
    a silent empty turn (EMOTION.md §8 / ARCHITECTURE §Error handling)."""


def _coerce_emotion(value: object, repairs: list[str]) -> Emotion:
    try:
        return Emotion(value)
    except ValueError:
        repairs.append(f"emotion {value!r} -> {DEFAULT_EMOTION.value}")
        return DEFAULT_EMOTION


def _coerce_intensity(value: object, repairs: list[str]) -> float:
    if value is None:
        repairs.append(f"intensity missing -> {DEFAULT_INTENSITY}")
        return DEFAULT_INTENSITY
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        repairs.append(f"intensity {value!r} -> {DEFAULT_INTENSITY}")
        return DEFAULT_INTENSITY
    clamped = min(1.0, max(0.0, number))
    if clamped != number:
        repairs.append(f"intensity {number} -> {clamped}")
    return clamped


def validate(
    raw: object,
    *,
    session_id: str | None = None,
    turn: int | None = None,
) -> EmotionState:
    """Validate/repair raw model output into a valid :class:`EmotionState` (EMOTION.md §8).

    The core never trusts raw output. Repairs (each **logged** keyed by
    ``session_id``/``turn``): an unknown/missing ``emotion`` → ``calm``;
    ``intensity`` clamped to ``[0, 1]``, missing/non-numeric → ``0.5``. A
    missing/empty ``reply`` raises :class:`EmotionError` — surfaced to the
    interface, not swallowed.
    """
    data = raw if isinstance(raw, dict) else {}
    reply = data.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        raise EmotionError("model returned no usable reply")

    repairs: list[str] = []
    emotion = _coerce_emotion(data.get("emotion"), repairs)
    intensity = _coerce_intensity(data.get("intensity"), repairs)
    if repairs:
        _log.warning(
            "emotion field repaired: %s",
            "; ".join(repairs),
            extra={"session_id": session_id, "turn": turn},
        )
    return EmotionState(reply=reply.strip(), emotion=emotion, intensity=intensity)


@runtime_checkable
class IEmotionRenderer(Protocol):
    """The one interface the core renders the emotion channel through (EMOTION.md §5).

    Each tier is one implementation — v0.3 :class:`LogRenderer`, v0.4 emoji (TUI),
    v2.1 image (web), v3.1 animation. **Only the renderer changes between versions;**
    ``EmotionState`` and the enum are constant.
    """

    def render(self, state: EmotionState) -> None:
        """Show the new state."""
        ...

    def set_speaking(self, speaking: bool) -> None:
        """v2.2+ voice → lip-sync (v3). No-op in the log/emoji tiers."""
        ...

    def tick(self, dt_ms: int) -> None:
        """v3 idle loop: transitions, micro-motion. No-op in the log/emoji tiers."""
        ...


class LogRenderer:
    """The v0.3 "logged" render tier — writes the validated field to the channel log.

    ``render`` logs ``{emotion, intensity}`` keyed by ``session_id``/``turn`` (set by
    the core each turn). ``set_speaking``/``tick`` are no-ops.
    """

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._log = logger or logging.getLogger("lumi.emotion.render")
        self.session_id: str | None = None
        self.turn: int | None = None

    def render(self, state: EmotionState) -> None:
        self._log.info(
            "emotion=%s intensity=%.2f",
            state.emotion.value,
            state.intensity,
            extra={
                "session_id": self.session_id,
                "turn": self.turn,
                "emotion": state.emotion.value,
                "intensity": state.intensity,
            },
        )

    def set_speaking(self, speaking: bool) -> None:
        pass  # v2.2+ voice → lip-sync; nothing to do in the log tier

    def tick(self, dt_ms: int) -> None:
        pass  # v3 idle loop; nothing to do in the log tier

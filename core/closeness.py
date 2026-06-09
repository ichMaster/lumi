"""Closeness — the per-user relationship level (v0.10).

The per-turn **relational read** (LUMI-038): the model scores the *user's* message on a
few dimensions, emitted **alongside** the locked v0.3 emotion field (additive — the emotion
contract is untouched). The core validates/clamps it here; it never blocks the reply.

The closeness engine (delta + bucketing + decay) and the authored levels build on this.
"""

from __future__ import annotations

from dataclasses import dataclass

# The relational dimensions the model scores on the user's message (each 0–1).
# warmth / vulnerability / playful RAISE closeness; harm / manipulation LOWER it.
RELATION_DIMS = ("warmth", "vulnerability", "playful", "harm", "manipulation")


@dataclass(frozen=True)
class RelationRead:
    """A read of the *user's* message on the relational dimensions (each in [0, 1]).

    Internal-only (raw scores are never shown to the user). Additive to the emotion
    field; missing/garbage dimensions degrade to ``0.0`` (never raises, never blocks).
    """

    warmth: float = 0.0
    vulnerability: float = 0.0
    playful: float = 0.0
    harm: float = 0.0
    manipulation: float = 0.0


def _clamp01(value: object) -> float:
    """A dimension coerced to a float in ``[0, 1]``; missing/garbage → ``0.0``."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, number))


def validate_relation(raw: object) -> RelationRead:
    """Validate/clamp a raw ``relation`` block into a :class:`RelationRead`.

    Each dimension is clamped to ``[0, 1]``; anything missing or non-numeric → ``0.0``.
    Never raises — a malformed (or absent) read degrades to an all-zero neutral read.
    """
    data = raw if isinstance(raw, dict) else {}
    return RelationRead(**{dim: _clamp01(data.get(dim)) for dim in RELATION_DIMS})

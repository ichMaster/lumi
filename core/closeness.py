"""Closeness — the per-user relationship level (v0.10).

The per-turn **relational read** (LUMI-038): the model scores the *user's* message on a
few dimensions, emitted **alongside** the locked v0.3 emotion field (additive — the emotion
contract is untouched). The core validates/clamps it here; it never blocks the reply.

The closeness engine (delta + bucketing + decay) and the authored levels build on this.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from core.repository import Closeness

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


# --- the closeness engine (LUMI-039) --------------------------------------
# A continuous value in [0, 100] → 5 levels of 20 each. Configurable knobs:
VALUE_MIN, VALUE_MAX = 0.0, 100.0
LEVELS = 5
BAND = (VALUE_MAX - VALUE_MIN) / LEVELS  # 20.0 per level
BASELINE = 30.0   # the resting value silence decays toward (mid-L2, "friendly")
DECAY_RETAINED = 0.9  # fraction of the gap-to-baseline kept per day of silence (exp decay)
W_POS = 1.0       # weight per positive dim (warmth/vulnerability/playful)
W_NEG = 1.5       # weight per negative dim (harm/manipulation) — harm costs more
DELTA_SCALE = 5.0  # value points per unit of weighted relational signal
INERTIA = 3.0     # dead-zone (points) around a band edge before the level flips


def _clamp_value(value: float) -> float:
    return min(VALUE_MAX, max(VALUE_MIN, value))


def naive_level(value: float) -> int:
    """The raw 1–5 band a value falls in (no inertia)."""
    return min(LEVELS, max(1, int(value // BAND) + 1))


def _bucket_with_inertia(value: float, current_level: int) -> int:
    """Re-bucket with hysteresis: the level flips only when the value is clearly into a
    new band (``INERTIA`` past the edge), so a single sharp turn doesn't flap the level."""
    target = naive_level(value)
    if target > current_level and value >= BAND * (target - 1) + INERTIA:
        return target  # promote: clearly inside the higher band
    if target < current_level and value <= BAND * target - INERTIA:
        return target  # demote: clearly inside the lower band
    return current_level


def _days_between(then: str, now: datetime) -> float:
    try:
        then_dt = datetime.fromisoformat(then)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, (now - then_dt).total_seconds() / 86400.0)


def update_closeness(
    current: Closeness | None, read: RelationRead, now: datetime, user_id: str
) -> Closeness:
    """Advance a user's closeness one turn: **decay** toward the baseline over days of
    silence, apply the relational **delta**, clamp, and **re-bucket** with inertia.

    Deterministic (the injected clock supplies ``now``). A brand-new user starts at the
    baseline (friendly). It biases warmth/openness only — **never competence**.
    """
    if current is None:
        value, level = BASELINE, naive_level(BASELINE)
    else:
        # decay the value toward the baseline by the days elapsed since last contact
        days = _days_between(current.last_ts, now)
        value = BASELINE + (current.value - BASELINE) * (DECAY_RETAINED**days)
        level = current.level
    # the per-turn relational delta: warmth/vulnerability/playful raise, harm/manipulation lower
    signal = W_POS * (read.warmth + read.vulnerability + read.playful) - W_NEG * (
        read.harm + read.manipulation
    )
    value = _clamp_value(value + DELTA_SCALE * signal)
    level = _bucket_with_inertia(value, level)
    return Closeness(user_id=user_id, value=value, level=level, last_ts=now.isoformat())

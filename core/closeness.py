"""Closeness — the per-user relationship level (v0.10).

The per-turn **relational read** (LUMI-038): the model scores the *user's* message on a
few dimensions, emitted **alongside** the locked v0.3 emotion field (additive — the emotion
contract is untouched). The core validates/clamps it here; it never blocks the reply.

The closeness engine (delta + bucketing + decay) and the authored levels build on this.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

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
DRIFT_RATE = 0.1  # per-turn pull toward the baseline — so an active warm streak can't pin at max

# Ephemeral daily mood-shift (a refinement of v0.10): today's emotional biorhythm + hormonal
# phase nudge the EFFECTIVE closeness level at prompt-assembly time ONLY — never persisted.
# It biases warmth/openness, NEVER competence; capped at ±one level band.
MOOD_SHIFT_MAX = BAND   # total daily shift capped at ±one band (20 points)
BIO_WEIGHT = 14.0       # the emotional biorhythm (−1…+1) contributes up to ±14 points
CYCLE_OFFSET = {        # the hormonal phase contributes up to ±6 points
    "овуляція": 6.0,
    "фолікулярна": 3.0,
    "лютеїнова": -2.0,
    "менструація": -4.0,
    "ПМС": -6.0,
}


@dataclass(frozen=True)
class ClosenessTuning:
    """The engine's behavioral knobs (config/.env-tunable). Defaults = the constants above."""

    baseline: float = BASELINE
    decay_retained: float = DECAY_RETAINED
    w_pos: float = W_POS
    w_neg: float = W_NEG
    delta_scale: float = DELTA_SCALE
    inertia: float = INERTIA
    drift_rate: float = DRIFT_RATE
    mood_shift_scale: float = 1.0  # 0..1 strength of the daily mood-shift (0 = off, 1 = full ±1 band)


_DEFAULT_TUNING = ClosenessTuning()  # shared immutable default (avoids a call in arg defaults)


def _clamp_value(value: float) -> float:
    return min(VALUE_MAX, max(VALUE_MIN, value))


def naive_level(value: float) -> int:
    """The raw 1–5 band a value falls in (no inertia)."""
    return min(LEVELS, max(1, int(value // BAND) + 1))


def _bucket_with_inertia(value: float, current_level: int, inertia: float) -> int:
    """Re-bucket with hysteresis: the level flips only when the value is clearly into a
    new band (``inertia`` past the edge), so a single sharp turn doesn't flap the level."""
    target = naive_level(value)
    if target > current_level and value >= BAND * (target - 1) + inertia:
        return target  # promote: clearly inside the higher band
    if target < current_level and value <= BAND * target - inertia:
        return target  # demote: clearly inside the lower band
    return current_level


def _days_between(then: str, now: datetime) -> float:
    try:
        then_dt = datetime.fromisoformat(then)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, (now - then_dt).total_seconds() / 86400.0)


def update_closeness(
    current: Closeness | None,
    read: RelationRead,
    now: datetime,
    user_id: str,
    tuning: ClosenessTuning = _DEFAULT_TUNING,
) -> Closeness:
    """Advance a user's closeness one turn: **decay** toward the baseline over days of
    silence, a small **per-turn drift** toward it (so an active warm streak settles at a high
    plateau instead of pinning at the top), apply the relational **delta**, clamp, and
    **re-bucket** with inertia.

    Deterministic (the injected clock supplies ``now``). A brand-new user starts at the
    baseline (friendly). It biases warmth/openness only — **never competence**.
    """
    if current is None:
        value, level = tuning.baseline, naive_level(tuning.baseline)
    else:
        # decay the value toward the baseline by the days elapsed since last contact
        days = _days_between(current.last_ts, now)
        value = tuning.baseline + (current.value - tuning.baseline) * (tuning.decay_retained**days)
        # then a per-turn drift toward the baseline, so the top is never a stable resting
        # point: holding a high level needs sustained warmth (the delta below still pushes up).
        value += (tuning.baseline - value) * tuning.drift_rate
        level = current.level
    # the per-turn relational delta: warmth/vulnerability/playful raise, harm/manipulation lower
    signal = tuning.w_pos * (read.warmth + read.vulnerability + read.playful) - tuning.w_neg * (
        read.harm + read.manipulation
    )
    value = _clamp_value(value + tuning.delta_scale * signal)
    level = _bucket_with_inertia(value, level, tuning.inertia)
    return Closeness(user_id=user_id, value=value, level=level, last_ts=now.isoformat())


def mood_shift(emotional: float | None, cycle_phase: str | None, scale: float = 1.0) -> float:
    """Today's **ephemeral** closeness shift in value points (±``MOOD_SHIFT_MAX``).

    Drawn from the **emotional biorhythm** (``emotional`` ∈ −1…+1, the warmth/mood cycle) and
    the **hormonal phase** (``cycle_phase``) — the two deterministic body rhythms. Applied
    **only** when assembling the prompt (``effective = base + shift``), **never persisted**, so
    a good-cycle day reads a notch warmer and a PMS/low day a notch more reserved without moving
    the real relationship. The intellectual/physical biorhythms are excluded on purpose (the
    rule: closeness biases warmth/openness, **never competence**). ``0.0`` when both are absent.

    ``scale`` (``LUMI_CLOSENESS_MOOD_SHIFT``, 0..1) tunes the strength: ``1`` = full ±1 band,
    ``0.5`` = half, ``0`` = the daily shift is off (``effective = base``).
    """
    bio = BIO_WEIGHT * emotional if emotional is not None else 0.0
    cyc = CYCLE_OFFSET.get(cycle_phase or "", 0.0)
    return max(-MOOD_SHIFT_MAX, min(MOOD_SHIFT_MAX, scale * (bio + cyc)))


def shifted_level(base_value: float, shift: float) -> int:
    """The 1–5 level for today's **effective** closeness = ``base_value + shift``.

    No inertia — this is a transient daily read for the prompt, not the persisted trajectory
    (the stored ``value``/``level`` are advanced by :func:`update_closeness` and untouched here).
    """
    return naive_level(_clamp_value(base_value + shift))


# The level a user sits at before any closeness record exists (the engine's starting point).
DEFAULT_LEVEL = naive_level(BASELINE)

# Framing for the active level's authored behavior block — prominent, like the mood header.
# It shapes warmth/openness/initiative ONLY; the guardrail (never competence) is in the text.
CLOSENESS_HEADER = (
    "Рівень близькості з цією людиною зараз — «{name}». Він задає лише теплоту, відкритість, "
    "ініціативу й грайливість — НІКОЛИ не твою компетентність, чесність чи готовність допомогти "
    "(ти однаково уважна й корисна на будь-якому рівні):"
)

# A level header: "## 1. Ввічлива" → (1, "Ввічлива").
_LEVEL_RE = re.compile(r"^##\s*(\d+)\.\s*(.+?)\s*$")


def load_levels(path: str | Path) -> dict[int, tuple[str, str]]:
    """Parse the authored ``core/closeness.md`` into ``{level: (name, behavior_text)}``.

    A missing/empty file yields ``{}`` (the closeness block is then simply not injected).
    """
    p = Path(path)
    if not p.is_file():
        return {}
    levels: dict[int, tuple[str, str]] = {}
    level: int | None = None
    name = ""
    buf: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        match = _LEVEL_RE.match(line)
        if match:
            if level is not None:
                levels[level] = (name, "\n".join(buf).strip())
            level, name, buf = int(match.group(1)), match.group(2), []
        elif line.startswith("#"):
            continue  # comment / file header
        elif level is not None:
            buf.append(line)
    if level is not None:
        levels[level] = (name, "\n".join(buf).strip())
    return {lv: (nm, body) for lv, (nm, body) in levels.items() if body}


def closeness_block(levels: dict[int, tuple[str, str]], level: int) -> str | None:
    """Build the system-prompt block for ``level`` (header + authored behavior), or ``None``."""
    entry = levels.get(level)
    if entry is None:
        return None
    name, behavior = entry
    return f"{CLOSENESS_HEADER.format(name=name)}\n{behavior}"


def level_name(levels: dict[int, tuple[str, str]], level: int) -> str | None:
    """The authored name for ``level`` (for ``/closeness``), or ``None``."""
    entry = levels.get(level)
    return entry[0] if entry else None

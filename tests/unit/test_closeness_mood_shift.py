"""Closeness refinement (of v0.10): per-turn drift to baseline + the ephemeral mood-shift.

The slow **base** can't pin at max (a per-turn drift toward the baseline), and today's
**mood-shift** (emotional biorhythm + cycle phase) colors the EFFECTIVE level at prompt time
only — never persisted. Warmth/openness only, never competence.
"""

from datetime import UTC, datetime

from core.agent import Core
from core.biorhythm import Biorhythms, Cycle
from core.clock import fixed_clock
from core.closeness import (
    BAND,
    BASELINE,
    ClosenessTuning,
    RelationRead,
    mood_shift,
    naive_level,
    shifted_level,
    update_closeness,
)
from core.cycle import CyclePhase
from core.llm import MockLLMClient
from core.repository import Closeness
from state.local_store import JsonRepository

NOW = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)


def _at(value, level, ts=NOW):
    return Closeness("owner", value, level, ts.isoformat())


# --- per-turn drift toward the baseline ------------------------------------
def test_per_turn_drift_pulls_toward_baseline_in_the_same_moment():
    # default drift 0.1: 70 + (30 − 70)·0.1 = 66 — the top is never a stable resting point
    c = update_closeness(_at(70.0, 4), RelationRead(), NOW, "owner")
    assert c.value == 66.0


def test_drift_is_zero_at_the_baseline():
    c = update_closeness(_at(BASELINE, 2), RelationRead(), NOW, "owner")
    assert c.value == BASELINE  # already at baseline → nothing to drift


def test_drift_rate_zero_restores_the_old_noop():
    c = update_closeness(_at(70.0, 4), RelationRead(), NOW, "owner", ClosenessTuning(drift_rate=0.0))
    assert c.value == 70.0  # drift off → unchanged within the same moment


def test_warm_streak_plateaus_below_max_not_pinned():
    # sustained moderate warmth: the drift balances the +delta, settling well below the ceiling
    c = _at(95.0, 5)
    for _ in range(25):
        c = update_closeness(c, RelationRead(warmth=0.6, playful=0.4), NOW, "owner")
    assert c.value < 90.0  # never pinned at 100 — "не залипає на максі"


# --- the ephemeral mood-shift (emotional biorhythm + cycle phase) ----------
def test_mood_shift_from_the_emotional_biorhythm():
    assert mood_shift(1.0, None) == 14.0    # emotional peak → +14
    assert mood_shift(-1.0, None) == -14.0  # trough → −14
    assert mood_shift(0.0, None) == 0.0


def test_mood_shift_adds_the_cycle_offset():
    assert mood_shift(0.0, "овуляція") == 6.0
    assert mood_shift(0.0, "ПМС") == -6.0
    assert mood_shift(None, "менструація") == -4.0


def test_mood_shift_is_capped_at_one_level_band():
    assert mood_shift(1.0, "овуляція") == BAND     # +14 + 6 = +20, one band warmer
    assert mood_shift(-1.0, "ПМС") == -BAND        # −14 − 6 = −20, one band cooler


def test_mood_shift_absent_or_unknown_inputs_are_neutral():
    assert mood_shift(None, None) == 0.0
    assert mood_shift(None, "не-фаза") == 0.0


def test_shifted_level_buckets_base_plus_shift_without_inertia():
    assert shifted_level(75.0, 20.0) == naive_level(95.0)   # L4 base, +20 → L5
    assert shifted_level(75.0, -20.0) == naive_level(55.0)  # L4 base, −20 → L3
    assert shifted_level(95.0, 20.0) == 5                   # clamps at the top
    assert shifted_level(10.0, -20.0) == 1                  # clamps at the bottom


# --- the prompt uses the EFFECTIVE level; storage stays the base -----------
def test_prompt_uses_effective_level_but_storage_keeps_the_base(tmp_path):
    repo = JsonRepository(tmp_path / "s.json")
    repo.set_closeness(Closeness("owner", 50.0, 3, NOW.isoformat()))  # base L3 (value 50)
    core = Core(
        llm=MockLLMClient(states={
            "reply": "ок", "emotion": "calm", "intensity": 0.4, "relation": {},  # neutral read
        }),
        repository=repo, canon="C", model="m", clock=fixed_clock(NOW),
        closeness_levels={3: ("Третя", "поведінка L3"), 4: ("Четверта", "поведінка L4")},
        closeness_enabled=True, mood_enabled=False,
    )
    # inject today's strongest warm shift: emotional peak (+14) + ovulation (+6) = +20
    core._biorhythms = Biorhythms(
        Cycle("physical", 0.0, "low"), Cycle("emotional", 1.0, "high"), Cycle("intellectual", 0.0, "low")
    )
    core._cycle = CyclePhase(14, 28, "овуляція", "нота")

    core.reply("привіт", core.start_session())

    system = core.last_prompt["system"]
    assert "Четверта" in system        # effective level = bucket(50 + 20) = L4
    assert "Третя" not in system       # not the base L3 block
    # the shift is NOT persisted — storage advanced only by the (neutral) read + drift
    assert core.closeness.value == 48.0 and core.closeness.level == 3  # 50 + (30−50)·0.1, still L3

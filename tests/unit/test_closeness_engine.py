"""The closeness engine (v0.10, LUMI-039) — delta, bucketing/inertia, time decay."""

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.closeness import (
    BASELINE,
    ClosenessTuning,
    RelationRead,
    naive_level,
    update_closeness,
)
from core.llm import MockLLMClient
from core.repository import Closeness
from state.local_store import JsonRepository

NOW = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)


def _at(value, level, ts=NOW):
    return Closeness("owner", value, level, ts.isoformat())


# --- delta -----------------------------------------------------------------
def test_new_user_starts_at_baseline():
    c = update_closeness(None, RelationRead(), NOW, "owner")
    assert c.value == BASELINE and c.level == naive_level(BASELINE)


def test_warmth_and_vulnerability_raise_the_value():
    c = update_closeness(_at(30.0, 2), RelationRead(warmth=0.8, vulnerability=0.6), NOW, "owner")
    assert c.value > 30.0


def test_harm_and_manipulation_lower_the_value():
    c = update_closeness(_at(50.0, 3), RelationRead(harm=0.8, manipulation=0.5), NOW, "owner")
    assert c.value < 50.0


# --- bucketing with inertia ------------------------------------------------
def test_level_does_not_flap_just_past_an_edge():
    # 39 + (warmth 0.4 → +2) = 41 — naive L3, but within INERTIA of the 40 edge → stays L2
    c = update_closeness(_at(39.0, 2), RelationRead(warmth=0.4), NOW, "owner")
    assert c.value == 41.0 and c.level == 2


def test_level_promotes_when_clearly_past_the_edge():
    # 39 + (warmth 1.0 → +5) = 44 ≥ 40 + INERTIA → promote to L3
    c = update_closeness(_at(39.0, 2), RelationRead(warmth=1.0), NOW, "owner")
    assert c.level == 3


def test_level_demotes_only_when_clearly_below_the_edge():
    held = update_closeness(_at(41.0, 3), RelationRead(harm=0.2), NOW, "owner")  # 39.5 → still L3
    assert held.value == 39.5 and held.level == 3
    dropped = update_closeness(_at(41.0, 3), RelationRead(harm=0.6), NOW, "owner")  # 36.5 → L2
    assert dropped.level == 2


# --- time decay ------------------------------------------------------------
def test_value_decays_toward_baseline_over_days_of_silence():
    start = _at(90.0, 5, datetime(2026, 6, 1, 12, 0, tzinfo=UTC))  # 8 days ago
    c = update_closeness(start, RelationRead(), NOW, "owner")  # neutral read
    assert 50.0 < c.value < 60.0 and c.value < 90.0  # eased toward baseline 30


def test_no_decay_within_the_same_moment():
    c = update_closeness(_at(70.0, 4), RelationRead(), NOW, "owner")  # last_ts == now
    assert c.value == 70.0  # no time elapsed, neutral read → unchanged


# --- tuning knobs (config/.env-driven) ------------------------------------
def test_tuning_sets_a_new_user_baseline():
    c = update_closeness(None, RelationRead(), NOW, "owner", ClosenessTuning(baseline=80.0))
    assert c.value == 80.0 and c.level == naive_level(80.0)


def test_tuning_delta_scale_amplifies_moves():
    c = update_closeness(_at(30.0, 2), RelationRead(warmth=0.5), NOW, "owner",
                         ClosenessTuning(delta_scale=20.0))
    assert c.value == 40.0  # 30 + 20 * 0.5


# --- the reply turn wires it -----------------------------------------------
def test_reply_updates_and_persists_closeness(tmp_path):
    core = Core(
        llm=MockLLMClient(states={
            "reply": "♥", "emotion": "tender", "intensity": 0.7,
            "relation": {"warmth": 0.9, "vulnerability": 0.7},
        }),
        repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m",
        clock=fixed_clock(NOW), mood_enabled=False, biorhythms_enabled=False, cycle_enabled=False,
    )
    assert core.closeness is None
    core.reply("я тобі довіряю", core.start_session())
    assert core.closeness is not None and core.closeness.value > BASELINE  # warmth raised it

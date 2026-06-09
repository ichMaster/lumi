"""Authored closeness levels + prompt block + guardrail (v0.10, LUMI-040)."""

from core.agent import Core
from core.closeness import (
    CLOSENESS_HEADER,
    DEFAULT_LEVEL,
    closeness_block,
    level_name,
    load_levels,
)
from core.config import DEFAULT_CLOSENESS_PATH
from core.llm import MockLLMClient
from core.repository import Closeness
from state.local_store import JsonRepository

LEVELS = load_levels(DEFAULT_CLOSENESS_PATH)


def _core(tmp_path, states):
    return Core(
        llm=MockLLMClient(states=states), repository=JsonRepository(tmp_path / "s.json"),
        canon="Ти — Лілі.", model="m", closeness_levels=LEVELS,
        mood_enabled=False, biorhythms_enabled=False, cycle_enabled=False,
    )


# --- authored levels -------------------------------------------------------
def test_load_levels_parses_five_named_levels():
    assert set(LEVELS) == {1, 2, 3, 4, 5}
    assert LEVELS[1][0] == "Ввічлива" and LEVELS[5][0] == "Найрідніша"
    assert all(body for _, body in LEVELS.values())  # every level has a behavior directive


def test_closeness_block_has_header_and_behavior():
    block = closeness_block(LEVELS, 4)
    assert "Близька" in block and "компетентн" in block.lower()  # name + guardrail framing


def test_closeness_block_missing_level_is_none():
    assert closeness_block({}, 3) is None and level_name({}, 3) is None


# --- guardrail: never competence ------------------------------------------
def test_guardrail_present_in_header_and_l1():
    # the header (rides on every level) and L1's own text both protect competence/helpfulness
    assert "компетентн" in CLOSENESS_HEADER.lower()
    assert "корисн" in LEVELS[1][1].lower() or "допом" in LEVELS[1][1].lower()


# --- prompt injection ------------------------------------------------------
def test_active_level_block_injected_in_prompt(tmp_path):
    core = _core(tmp_path, {"reply": "ок", "emotion": "calm", "intensity": 0.5})
    sysp = core._system_prompt(core.start_session())  # no record yet → DEFAULT_LEVEL
    assert LEVELS[DEFAULT_LEVEL][0] in sysp and "компетентн" in sysp


def test_low_closeness_still_answers_fully(tmp_path):
    core = _core(tmp_path, {"reply": "Звісно, ось відповідь.", "emotion": "calm", "intensity": 0.5})
    core._repo.set_closeness(Closeness("owner", 5.0, 1, "2026-06-09T10:00:00+00:00"))  # L1 reserved
    state = core.reply("допоможи мені з кодом", core.start_session())
    assert state.reply == "Звісно, ось відповідь."  # full help — closeness never refuses
    assert "Ввічлива" in core.last_prompt["system"]  # the L1 (reserved-but-helpful) block rode along


# --- /closeness surface (LUMI-041) ----------------------------------------
def test_closeness_status_defaults_when_no_record(tmp_path):
    core = _core(tmp_path, {"reply": "ок", "emotion": "calm", "intensity": 0.5})
    level, name = core.closeness_status()  # no record yet → the default level, by name
    assert level == DEFAULT_LEVEL and name == LEVELS[DEFAULT_LEVEL][0]


def test_closeness_status_reads_the_record_by_name(tmp_path):
    core = _core(tmp_path, {"reply": "ок", "emotion": "calm", "intensity": 0.5})
    core._repo.set_closeness(Closeness("owner", 85.0, 5, "2026-06-09T10:00:00+00:00"))
    assert core.closeness_status() == (5, "Найрідніша")  # level + name only (no raw value)

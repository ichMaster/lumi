"""The per-turn relational read (v0.10, LUMI-038) — additive to the emotion field."""

from core.agent import Core
from core.closeness import RELATION_DIMS, RelationRead, validate_relation
from core.llm import _EMOTION_TOOL, MockLLMClient
from state.local_store import JsonRepository


# --- validate_relation ----------------------------------------------------
def test_validate_relation_full_dict():
    r = validate_relation(
        {"warmth": 0.9, "vulnerability": 0.4, "playful": 0.2, "harm": 0.0, "manipulation": 0.1}
    )
    assert (r.warmth, r.vulnerability, r.playful, r.harm, r.manipulation) == (0.9, 0.4, 0.2, 0.0, 0.1)


def test_validate_relation_clamps_out_of_range():
    r = validate_relation({"warmth": 5.0, "harm": -2.0})
    assert r.warmth == 1.0 and r.harm == 0.0


def test_validate_relation_missing_and_garbage_degrade_to_zero():
    r = validate_relation({"warmth": "lots", "playful": None})  # garbage / missing
    assert r == RelationRead()  # all zeros


def test_validate_relation_non_dict_is_neutral():
    assert validate_relation(None) == RelationRead()
    assert validate_relation("nope") == RelationRead()


# --- the tool schema: emotion contract unchanged, relation additive -------
def test_emotion_contract_required_is_unchanged():
    # v0.3 lock: only reply/emotion/intensity are required — relation is additive (optional).
    assert _EMOTION_TOOL["input_schema"]["required"] == ["reply", "emotion", "intensity"]


def test_relation_is_an_additive_optional_property_with_the_dims():
    props = _EMOTION_TOOL["input_schema"]["properties"]
    assert "relation" in props and "relation" not in _EMOTION_TOOL["input_schema"]["required"]
    assert set(props["relation"]["properties"]) == set(RELATION_DIMS)


# --- the reply turn reads it (additive; emotion intact) -------------------
def _core(tmp_path, states):
    return Core(
        llm=MockLLMClient(states=states), repository=JsonRepository(tmp_path / "s.json"),
        canon="Ти — Лілі.", model="m", mood_enabled=False, biorhythms_enabled=False,
        cycle_enabled=False,
    )


def test_reply_populates_last_relation_without_touching_emotion(tmp_path):
    core = _core(tmp_path, {
        "reply": "привіт", "emotion": "joy", "intensity": 0.8,
        "relation": {"warmth": 0.9, "vulnerability": 0.5, "playful": 0.3, "harm": 0.0, "manipulation": 0.0},
    })
    state = core.reply("обіймаю тебе", core.start_session())
    assert state.emotion.value == "joy" and state.intensity == 0.8  # emotion field unchanged
    assert core.last_relation.warmth == 0.9 and core.last_relation.vulnerability == 0.5


def test_reply_without_relation_degrades_to_neutral(tmp_path):
    core = _core(tmp_path, {"reply": "ок", "emotion": "calm", "intensity": 0.5})  # no relation
    core.reply("привіт", core.start_session())
    assert core.last_relation == RelationRead()  # neutral, never raises


def test_relation_instruction_in_prompt(tmp_path):
    core = _core(tmp_path, {"reply": "ок", "emotion": "calm", "intensity": 0.5})
    sysp, _ = core._system_prompt(core.start_session())
    assert "relation" in sysp and "warmth" in sysp  # the model is asked to fill it

"""Unit tests for the emotion validation/fallback gate (LUMI-015, EMOTION.md §8).

The single place emotion can go wrong — driven with deliberately malformed input.
"""

import logging

import pytest

from core.emotion import DEFAULT_INTENSITY, Emotion, EmotionError, validate


def test_valid_state_passes_through():
    state = validate({"reply": "Привіт!", "emotion": "joy", "intensity": 0.8})
    assert state.reply == "Привіт!"
    assert state.emotion is Emotion.JOY
    assert state.intensity == 0.8


def test_unknown_emotion_falls_back_to_calm():
    state = validate({"reply": "ок", "emotion": "ecstatic", "intensity": 0.5})
    assert state.emotion is Emotion.CALM


def test_missing_emotion_falls_back_to_calm():
    assert validate({"reply": "ок", "intensity": 0.5}).emotion is Emotion.CALM


def test_intensity_is_clamped_to_unit_range():
    assert validate({"reply": "a", "emotion": "joy", "intensity": 5}).intensity == 1.0
    assert validate({"reply": "a", "emotion": "joy", "intensity": -2}).intensity == 0.0


def test_missing_or_non_numeric_intensity_defaults():
    assert validate({"reply": "a", "emotion": "joy"}).intensity == DEFAULT_INTENSITY
    assert validate({"reply": "a", "emotion": "joy", "intensity": "loud"}).intensity == DEFAULT_INTENSITY


def test_missing_reply_raises():
    with pytest.raises(EmotionError):
        validate({"emotion": "joy", "intensity": 0.5})


def test_empty_or_whitespace_reply_raises():
    with pytest.raises(EmotionError):
        validate({"reply": "   ", "emotion": "joy", "intensity": 0.5})


def test_non_dict_raw_raises():
    with pytest.raises(EmotionError):
        validate(None)


def test_accepts_an_emotion_member_value():
    # The structured path may hand back the enum value already.
    state = validate({"reply": "a", "emotion": Emotion.PLAYFUL, "intensity": 0.3})
    assert state.emotion is Emotion.PLAYFUL


def test_repairs_are_logged_keyed_by_session(caplog):
    with caplog.at_level(logging.WARNING, logger="lumi.emotion"):
        validate(
            {"reply": "a", "emotion": "nope", "intensity": 9},
            session_id="s1",
            turn=3,
        )
    rec = next(r for r in caplog.records if r.name == "lumi.emotion")
    assert "emotion" in rec.getMessage() and "intensity" in rec.getMessage()
    assert rec.session_id == "s1" and rec.turn == 3

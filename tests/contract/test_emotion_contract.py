"""Contract test for the emotion channel (v0.3 — EMOTION.md §3–§4).

Pins the **fixed 9-value enum** and the **EmotionState field schema** — the
contract every render tier reuses. Changing the enum membership or the field set
must change this test (ARCHITECTURE §Contracts).
"""

from dataclasses import FrozenInstanceError, fields, is_dataclass

import pytest

from core.emotion import DEFAULT_EMOTION, Emotion, EmotionState

# The exact 9 emotion names from EMOTION.md §4 (no more, no fewer).
NINE = {
    "joy", "calm", "playful", "tender", "thoughtful",
    "serious", "surprise", "doubt", "sad",
}


def test_emotion_enum_is_exactly_the_nine():
    assert {e.value for e in Emotion} == NINE
    assert len(Emotion) == 9


def test_calm_is_the_neutral_fallback():
    assert DEFAULT_EMOTION is Emotion.CALM
    assert DEFAULT_EMOTION.value == "calm"


def test_emotion_members_are_their_wire_value():
    # str mixin → the member equals its lowercase wire string.
    assert Emotion.JOY == "joy"
    assert Emotion("playful") is Emotion.PLAYFUL


def test_emotion_state_shape_is_locked():
    assert is_dataclass(EmotionState)
    assert {f.name for f in fields(EmotionState)} == {"reply", "emotion", "intensity"}


def test_emotion_state_is_immutable():
    state = EmotionState(reply="привіт", emotion=Emotion.JOY, intensity=0.8)
    with pytest.raises(FrozenInstanceError):
        state.reply = "змінено"  # frozen dataclass

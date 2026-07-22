"""Unit tests for the v1.1 conversation-style enum + validation."""

import pytest

from core.intent import INTENTS, validate_intent


def test_the_authored_styles():
    # The 7 authored v1.1 moves + `mutate` (v1.5.x — the Алхімік voice's lateral «а що якщо…»).
    assert INTENTS == (
        "deepen", "position", "object", "develop", "associate", "example", "return", "mutate",
    )


@pytest.mark.parametrize("value", INTENTS)
def test_every_valid_style_passes(value):
    assert validate_intent(value) == value


@pytest.mark.parametrize(
    "bad", ["заглибити", "", "DEEPEN", "move", None, 7, 0.5, ["заглибити"], {"style": "заглибити"}]
)
def test_unknown_or_garbled_is_dropped_silently(bad):
    assert validate_intent(bad) is None


def test_json_state_instruction_names_the_style_field():
    # JSON/schema providers (OpenAI, Gemini) obey the strict "ONLY … required keys" wording —
    # without the field named there they drop what the instruction asked for.
    from core.llm import _JSON_STATE_INSTRUCTION

    assert '"intent"' in _JSON_STATE_INSTRUCTION
    for value in INTENTS:
        assert value in _JSON_STATE_INSTRUCTION

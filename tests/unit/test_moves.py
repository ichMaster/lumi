"""Unit tests for the v1.1 conversation-move enum + validation (LUMI-175)."""

import pytest

from core.moves import MOVES, validate_move


def test_the_seven_authored_moves():
    # The closed English enum (the emotion-enum precedent); UA names are display labels only.
    assert MOVES == ("deepen", "position", "object", "develop", "associate", "example", "return")


@pytest.mark.parametrize("value", MOVES)
def test_every_valid_move_passes(value):
    assert validate_move(value) == value


@pytest.mark.parametrize("bad", ["sonnet", "", "DEEPEN", "заглибити", None, 7, 0.5, ["deepen"], {"move": "deepen"}])
def test_unknown_or_garbled_is_dropped_silently(bad):
    assert validate_move(bad) is None

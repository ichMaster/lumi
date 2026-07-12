"""Unit tests for the v1.1 conversation-move enum, validation (LUMI-175) and the
arbiter's data-visible dynamics (LUMI-177)."""

from dataclasses import dataclass

import pytest

from core.moves import MOVES, SHORT_REACTION_CHARS, arbiter_dynamics, validate_move


@dataclass
class _Msg:
    role: str
    text: str
    move: str | None = None


def test_the_seven_authored_moves():
    # The closed English enum (the emotion-enum precedent); UA names are display labels only.
    assert MOVES == ("deepen", "position", "object", "develop", "associate", "example", "return")


@pytest.mark.parametrize("value", MOVES)
def test_every_valid_move_passes(value):
    assert validate_move(value) == value


@pytest.mark.parametrize("bad", ["sonnet", "", "DEEPEN", "заглибити", None, 7, 0.5, ["deepen"], {"move": "deepen"}])
def test_unknown_or_garbled_is_dropped_silently(bad):
    assert validate_move(bad) is None


# --- LUMI-177: arbiter_dynamics ------------------------------------------------------------

_LONG = "Це доволі розгорнута відповідь, значно довша за поріг короткої реакції."


def test_dynamics_empty_on_no_data():
    assert arbiter_dynamics([]) == ""
    # untyped history (moves off / pre-v1.1 records) with long reactions → nothing applies
    window = [_Msg("user", _LONG), _Msg("lili", "ok"), _Msg("user", _LONG), _Msg("lili", "ok")]
    assert arbiter_dynamics(window) == ""


def test_dynamics_lists_declared_types():
    window = [
        _Msg("user", _LONG), _Msg("lili", "a", move="deepen"),
        _Msg("user", _LONG), _Msg("lili", "b", move="position"),
    ]
    block = arbiter_dynamics(window)
    assert "deepen, position" in block
    assert "двічі поспіль" not in block  # different types → no ban


def test_dynamics_bans_a_type_declared_twice_in_a_row():
    window = [
        _Msg("user", _LONG), _Msg("lili", "a", move="deepen"),
        _Msg("user", _LONG), _Msg("lili", "b", move="deepen"),
    ]
    block = arbiter_dynamics(window)
    assert "«deepen» заявлено двічі поспіль" in block and "НЕ обирай" in block


def test_dynamics_no_ban_when_a_gap_is_untyped():
    # None between equal types breaks the "in a row" pair — no ban.
    window = [
        _Msg("user", _LONG), _Msg("lili", "a", move="deepen"),
        _Msg("user", _LONG), _Msg("lili", "b"),
    ]
    assert "двічі поспіль" not in arbiter_dynamics(window)


def test_dynamics_topic_died_on_two_short_reactions():
    short = "ок"
    assert len(short) <= SHORT_REACTION_CHARS
    window = [
        _Msg("lili", "a", move="deepen"), _Msg("user", short),
        _Msg("lili", "b", move="example"), _Msg("user", short),
    ]
    block = arbiter_dynamics(window)
    assert "вичерпана" in block and "associate" in block and "return" in block


def test_dynamics_no_topic_died_on_long_reactions():
    window = [
        _Msg("lili", "a", move="deepen"), _Msg("user", _LONG),
        _Msg("lili", "b", move="example"), _Msg("user", _LONG),
    ]
    assert "вичерпана" not in arbiter_dynamics(window)


def test_dynamics_never_raises_on_a_bad_window():
    assert arbiter_dynamics(None) == ""  # degrade to empty, never block a turn

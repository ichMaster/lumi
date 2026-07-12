"""Conversation moves (v1.1): the closed move enum + its validation.

A *move* is the type of conversational act Лілі's reply performs — one of 7 authored
values (English identifiers, the emotion-enum precedent; the UA display names live in
the concept doc). The model returns it as an **additive optional ``move`` field on
``set_state``** (the v0.10 ``relation`` pattern): the locked ``{reply, emotion,
intensity}`` contract is untouched, and an unknown/missing/garbled value is dropped
silently — never an error, never a blocked turn.
"""

from __future__ import annotations

# The closed set. One source of truth — the set_state schemas (core/llm.py) and the
# arbiter dynamics (v1.1 LUMI-177) all read this tuple.
MOVES: tuple[str, ...] = (
    "deepen",     # заглибити — конкретне питання про аспект сказаного
    "position",   # позиція — твердження від першої особи
    "object",     # заперечити — незгода зі сказаним або припущеним
    "develop",    # розвинути — наступний логічний крок з думки співрозмовника
    "associate",  # асоціація — власний матеріал (думки, минулі розмови, теми)
    "example",    # приклад — потягнути з абстрактного в конкретне
    "return",     # повернутись — дістати відкрите питання зі старої теми
)


def validate_move(raw: object) -> str | None:
    """The validated move value, or ``None`` — unknown/missing/non-string dropped silently."""
    return raw if isinstance(raw, str) and raw in MOVES else None

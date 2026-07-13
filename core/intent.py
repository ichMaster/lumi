"""Conversation style (v1.1): the closed style enum + its validation.

Лілі's arbiter (the think-phase) picks ONE **conversation style** per reply — how to
continue the conversation, out of 7 authored ways. The model returns it as an **additive
optional ``intent`` field on ``set_state``** (the v0.10 ``relation`` pattern):
the locked ``{reply, emotion, intensity}`` contract is untouched, and an unknown/missing/
garbled value is dropped silently — never an error, never a blocked turn. The chosen style
is stored on her message and replayed next turn so the **retrospective** can check whether
the reply actually did what it declared.
"""

from __future__ import annotations

# The closed set — the 7 reply intents. English enum values (like the emotion enum), stored
# in store.json; the Ukrainian glosses live in the think instruction. One source of truth: the
# set_state schema (core/llm.py) and the prompt instruction read this tuple.
INTENTS: tuple[str, ...] = (
    "deepen",     # заглибити — конкретне питання про аспект сказаного
    "position",   # позиція — власне твердження від першої особи
    "object",     # заперечити — незгода зі сказаним або припущеним
    "develop",    # розвинути — наступний логічний крок з думки співрозмовника
    "associate",  # асоціація — власний матеріал (думки, минулі розмови)
    "example",    # приклад — потягнути з абстрактного в конкретне
    "return",     # повернутись — відкрите питання зі старої теми
)


def validate_intent(raw: object) -> str | None:
    """The validated style value, or ``None`` — unknown/missing/non-string dropped silently."""
    return raw if isinstance(raw, str) and raw in INTENTS else None

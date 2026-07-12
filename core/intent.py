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

# The closed set — the 7 conversation styles (Ukrainian, jargon-free; the same words the
# think instruction and the retrospective use). One source of truth: the set_state schema
# (core/llm.py) and the prompt instruction read this tuple.
INTENTS: tuple[str, ...] = (
    "заглибити",    # конкретне питання про аспект сказаного
    "позиція",      # власне твердження від першої особи
    "заперечити",   # незгода зі сказаним або припущеним
    "розвинути",    # наступний логічний крок з думки співрозмовника
    "асоціація",    # власний матеріал (думки, минулі розмови)
    "приклад",      # потягнути з абстрактного в конкретне
    "повернутись",  # відкрите питання зі старої теми
)


def validate_intent(raw: object) -> str | None:
    """The validated style value, or ``None`` — unknown/missing/non-string dropped silently."""
    return raw if isinstance(raw, str) and raw in INTENTS else None

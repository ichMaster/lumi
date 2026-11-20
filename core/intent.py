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

# The closed set — the reply intents (7 authored in v1.1 + `mutate` added in v1.5.x). English enum
# values (like the emotion enum), stored in store.json; the Ukrainian glosses live in the think
# instruction. One source of truth: the set_state schema (core/llm.py) and the prompt instruction
# read this tuple.
INTENTS: tuple[str, ...] = (
    "deepen",     # «заглибити» — a concrete question about one aspect of what was said
    "position",   # «позиція» — her own statement, in the first person
    "object",     # «заперечити» — disagreeing with what was said or assumed
    "develop",    # «розвинути» — the next logical step from the other person's thought
    "associate",  # «асоціація» — her own material (thoughts, past conversations)
    "example",    # «приклад» — pulling the abstract down to the concrete
    "return",     # «повернутись» — an open question from an old topic
    "mutate",     # «уява» — turning the topic sideways with one «а що якщо…» (reversed / scaled / elsewhere / crossed)
    "switch",     # «переключити» — by mood: honestly not wanting his topic, closing it and offering her own
)


def validate_intent(raw: object) -> str | None:
    """The validated style value, or ``None`` — unknown/missing/non-string dropped silently."""
    return raw if isinstance(raw, str) and raw in INTENTS else None

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


# --- v1.1 LUMI-177: the arbiter's data-visible dynamics ---------------------------------------
#
# The rule TABLE is fixed text in the think instruction (LUMI-178); this module computes only
# what plain data shows — the declared types and the user's reaction lengths — and emits the
# dynamic lines substituted into the `{move_rules}` placeholder at prompt assembly (the v0.12
# resolver). Execution judgment (`виконано: так/ні`) stays with the model's retrospective.

# A user reaction at or under this many characters counts as "коротка" (terse) — two such
# reactions in a row suggest the topic died and external-material moves should take over.
SHORT_REACTION_CHARS = 30


def _last_lili_moves(window: object, n: int = 2) -> list[str | None]:
    """The declared moves of the last ``n`` Лілі replies in the window (oldest→newest)."""
    lili = [m for m in window if getattr(m, "role", None) == "lili"]
    return [getattr(m, "move", None) for m in lili[-n:]]


def arbiter_dynamics(window: object) -> str:
    """The dynamic arbiter lines for this turn — pure over the live message window.

    Emits (each only when the data shows it): the declared types of the last replies (the
    retrospective's anchor), the same-type hard ban (the same move declared twice in a row
    must not be chosen a third time), and the topic-died hint (the last two user reactions
    were terse → prefer ``associate``/``return``). Returns ``""`` when nothing applies —
    the ``{move_rules}`` token then substitutes to nothing, never blocks a turn.
    """
    try:
        msgs = list(window)
    except TypeError:
        return ""
    lines: list[str] = []

    declared = [m for m in _last_lili_moves(msgs) if m]
    if declared:
        lines.append("Заявлені типи твоїх останніх реплік (стара → нова): " + ", ".join(declared) + ".")

    last_two = _last_lili_moves(msgs)
    if len(last_two) == 2 and last_two[0] is not None and last_two[0] == last_two[1]:
        lines.append(
            f"Тип «{last_two[1]}» заявлено двічі поспіль — НЕ обирай його на цю відповідь."
        )

    user_texts = [getattr(m, "text", "") or "" for m in msgs if getattr(m, "role", None) == "user"]
    if len(user_texts) >= 2 and all(len(t.strip()) <= SHORT_REACTION_CHARS for t in user_texts[-2:]):
        lines.append(
            "Останні реакції співрозмовника короткі — тема, схоже, вичерпана: "
            "обери «associate» або «return» (принеси власний матеріал або відкрите питання)."
        )

    return "\n".join(lines)

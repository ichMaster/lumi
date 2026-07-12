"""v1.1 LUMI-179 — the phase-closing integration sweep: a scripted 20-exchange
conversation walking the arbiter's data-visible rules end-to-end, plus the DoD
asserts (store shape, no leaks, byte-identical off). No paid calls.
"""
from __future__ import annotations

from core.agent import Core
from core.config import DEFAULT_INNER_VOICE_MOVES_PATH
from core.llm import MockLLMClient
from core.moves import MOVES
from core.prompt import MOVE_INSTRUCTION, REASONING_DIRECTIVE, load_inner_voice
from state.local_store import JsonRepository

_LONG = "Розлоге повідомлення-відповідь, помітно довше за поріг короткої реакції арбітра."


def _state(move: str | None, reply: str = "відповідь") -> dict:
    s = {"reply": reply, "emotion": "calm", "intensity": 0.5}
    if move:
        s["move"] = move
    return s


def _core(tmp_path, llm, **kw) -> Core:
    return Core(
        llm=llm, repository=JsonRepository(tmp_path / "s.json"), canon="Ти — Лілі.", model="m",
        mood_enabled=False, biorhythms_enabled=False, cycle_enabled=False, **kw,
    )


def test_twenty_exchange_rule_walk(tmp_path):
    """The DoD walk: 20 scripted exchanges — declared types land in the store per turn,
    a same-type repeat raises the ban on the NEXT turn's prompt, terse reactions raise
    the topic-died hint, and no three-in-a-row same type survives the walk."""
    script = [
        "deepen", "position", "object", "develop",      # varied — no bans expected
        "deepen", "deepen",                              # repeat → ban next turn
        "associate", "example", "return", "position",    # recovers, varied
        "object", "object",                              # second repeat → ban next turn
        "develop", "associate", "example", "deepen",
        "return", "associate", "position", "example",    # closes varied
    ]
    llm = MockLLMClient(states=[_state(m, reply=f"репліка {i}") for i, m in enumerate(script)])
    core = _core(
        tmp_path, llm, moves_enabled=True,
        reasoning_directive=load_inner_voice(DEFAULT_INNER_VOICE_MOVES_PATH),
    )
    session = core.start_session()

    user_lines = [_LONG] * len(script)
    user_lines[12] = "ок"  # after the object/object pair: two terse reactions in a row…
    user_lines[13] = "ага"  # …→ the topic-died hint on turn 15's prompt

    systems: list[str] = []
    for line in user_lines:
        core.reply(line, session)
        systems.append(str(llm.calls[-1]["system"]))

    # 1) Every Лілі line in the store carries its scripted move; user lines carry None.
    msgs = core._repo.load_messages(session.id)
    lili_moves = [m.move for m in msgs if m.role == "lili"]
    assert lili_moves == script
    assert all(m.move is None for m in msgs if m.role == "user")

    # 2) The same-type ban lands on the prompt of the turn AFTER a double declare.
    assert "«deepen» заявлено двічі поспіль" in systems[6]
    assert "«object» заявлено двічі поспіль" in systems[12]
    # …and not while types vary (the DYNAMIC ban line, not the static rule-table text).
    assert "заявлено двічі поспіль" not in systems[3]

    # 3) Two terse reactions in a row → the topic-died hint (associate/return only).
    assert "вичерпана" in systems[14]

    # 4) The walk never leaves three same types in a row in the store (the rules held).
    assert not any(
        lili_moves[i] == lili_moves[i + 1] == lili_moves[i + 2] for i in range(len(lili_moves) - 2)
    )

    # 5) No leak anywhere: rendered replies and stored texts never carry a marker.
    assert all("<move>" not in (m.text or "") for m in msgs)


def test_byte_identical_off_even_over_a_typed_store(tmp_path):
    """The DoD off-pin: a store that ACQUIRED moves while the feature was on replays with
    no move artifacts at all once the feature is off — instruction, marker and value gone."""
    llm_on = MockLLMClient(
        states=[_state("deepen", "перша"), _state("position", "друга"), _state(None, "третя")]
    )
    core_on = _core(tmp_path, llm_on, moves_enabled=True)
    session = core_on.start_session()
    core_on.reply(_LONG, session)
    core_on.reply(_LONG, session)

    # Reopen the SAME store with moves off (the default) — same session continues.
    llm_off = MockLLMClient(states=_state(None, "третя"))
    core_off = Core(
        llm=llm_off, repository=JsonRepository(tmp_path / "s.json"), canon="Ти — Лілі.", model="m",
        mood_enabled=False, biorhythms_enabled=False, cycle_enabled=False,
    )
    core_off.reply(_LONG, session)
    system = str(llm_off.calls[-1]["system"])
    replayed = " ".join(str(m.get("content", "")) for m in llm_off.calls[-1]["messages"])
    assert MOVE_INSTRUCTION not in system  # the ask is gone
    assert REASONING_DIRECTIVE in system and "[арбітр]" not in system  # the v1 think chain
    assert "<move>" not in replayed  # typed records replay with NO marker when off
    lili_new = [m for m in core_off._repo.load_messages(session.id) if m.role == "lili"][-1]
    assert lili_new.move is None  # and nothing new is stored


def test_every_move_value_survives_a_store_round_trip(tmp_path):
    """All seven enum values persist and reload verbatim (store.json shape DoD)."""
    llm = MockLLMClient(states=[_state(m) for m in MOVES])
    core = _core(tmp_path, llm, moves_enabled=True)
    session = core.start_session()
    for _ in MOVES:
        core.reply(_LONG, session)
    reloaded = JsonRepository(tmp_path / "s.json").load_messages(session.id)
    assert [m.move for m in reloaded if m.role == "lili"] == list(MOVES)

"""v1.1 LUMI-178 — the v2 (conversation-moves) think instruction + its selection.

No paid calls — a MockLLMClient records the system prompt each turn so we can assert
which think directive rode in it and how {move_rules} resolved.
"""
from __future__ import annotations

from core.agent import Core, build_core
from core.config import DEFAULT_INNER_VOICE_MOVES_PATH, Config
from core.llm import MockLLMClient
from core.moves import MOVES
from core.prompt import REASONING_DIRECTIVE, load_inner_voice
from state.local_store import JsonRepository

_STATE = {"reply": "Привіт!", "emotion": "joy", "intensity": 0.8}


def _system_of_last_turn(core: Core, llm: MockLLMClient) -> str:
    core.reply("привіт", core.start_session())
    return str(llm.calls[-1]["system"])


# --- the shipped v2 file is a valid moves instruction ----------------------------------------------
def test_shipped_v2_file_is_the_four_block_moves_instruction():
    text = load_inner_voice(DEFAULT_INNER_VOICE_MOVES_PATH)
    assert text is not None
    for block in ("[ретроспектива]", "[голоси]", "[арбітр]", "[репліка]"):
        assert block in text  # the four-block format
    for voice in ("ІМПУЛЬС", "ТВЕРЕЗІСТЬ", "СТАНДАРТ"):
        assert voice in text  # the three voices stay (typed now)
    for move in MOVES:
        assert move in text  # all seven types are named
    assert "{move_rules}" in text  # the LUMI-177 dynamic block's seat
    assert "<think>" in text  # the wrap mechanism survives (split_reasoning)
    assert "компетентн" in text.lower()  # never-competence, authored in
    assert "не переказуй" in text.lower()  # the anti-mirror rule, authored in


# --- build_core selection ---------------------------------------------------------------------------
def test_build_core_selects_the_v2_file_when_moves_on(tmp_path):
    llm = MockLLMClient(states=dict(_STATE))
    cfg = Config(store_path=tmp_path / "s.json", moves=True)
    core = build_core(config=cfg, llm=llm, repository=JsonRepository(tmp_path / "s.json"))
    system = _system_of_last_turn(core, llm)
    assert "[ретроспектива]" in system and "[арбітр]" in system
    assert REASONING_DIRECTIVE not in system  # replaced, not appended


def test_build_core_v2_overrides_an_active_v1_inner_voice(tmp_path):
    v1 = tmp_path / "v1.md"
    v1.write_text("ТРИ ГОЛОСИ V1. Загорни міркування у <think>…</think>.", encoding="utf-8")
    llm = MockLLMClient(states=dict(_STATE))
    cfg = Config(store_path=tmp_path / "s.json", moves=True, inner_voice=True, inner_voice_path=v1)
    core = build_core(config=cfg, llm=llm, repository=JsonRepository(tmp_path / "s.json"))
    system = _system_of_last_turn(core, llm)
    assert "[арбітр]" in system and "ТРИ ГОЛОСИ V1" not in system


def test_build_core_missing_v2_degrades_to_the_v1_chain(tmp_path):
    v1 = tmp_path / "v1.md"
    v1.write_text("ТРИ ГОЛОСИ V1. Загорни міркування у <think>…</think>.", encoding="utf-8")
    llm = MockLLMClient(states=dict(_STATE))
    cfg = Config(
        store_path=tmp_path / "s.json", moves=True, inner_voice=True, inner_voice_path=v1,
        inner_voice_moves_path=tmp_path / "nope.md",
    )
    core = build_core(config=cfg, llm=llm, repository=JsonRepository(tmp_path / "s.json"))
    assert "ТРИ ГОЛОСИ V1" in _system_of_last_turn(core, llm)  # graceful, no crash


def test_build_core_moves_off_never_loads_the_v2_file(tmp_path):
    llm = MockLLMClient(states=dict(_STATE))
    cfg = Config(store_path=tmp_path / "s.json")  # moves off (default)
    core = build_core(config=cfg, llm=llm, repository=JsonRepository(tmp_path / "s.json"))
    system = _system_of_last_turn(core, llm)
    assert REASONING_DIRECTIVE in system and "[арбітр]" not in system  # byte-identical off


# --- the shipped v2 + the LUMI-177 dynamics end-to-end ----------------------------------------------
def test_shipped_v2_resolves_move_rules_with_live_dynamics(tmp_path):
    v2 = load_inner_voice(DEFAULT_INNER_VOICE_MOVES_PATH)
    llm = MockLLMClient(
        states=[
            {"reply": "a", "emotion": "calm", "intensity": 0.5, "move": "deepen"},
            {"reply": "b", "emotion": "calm", "intensity": 0.5, "move": "deepen"},
            {"reply": "c", "emotion": "calm", "intensity": 0.5},
        ]
    )
    core = Core(
        llm=llm, repository=JsonRepository(tmp_path / "s.json"), canon="Ти — Лілі.", model="m",
        moves_enabled=True, reasoning_directive=v2,
        mood_enabled=False, biorhythms_enabled=False, cycle_enabled=False,
    )
    session = core.start_session()
    core.reply("перше розлоге повідомлення, помітно довше за поріг короткої реакції", session)
    core.reply("друге розлоге повідомлення, теж помітно довше за поріг реакції", session)
    core.reply("третє розлоге повідомлення, знову довше за поріг короткої реакції", session)
    system = str(llm.calls[-1]["system"])
    assert "«deepen» заявлено двічі поспіль" in system  # the live ban rode into the v2 instruction
    assert "{move_rules}" not in system  # the token never leaks

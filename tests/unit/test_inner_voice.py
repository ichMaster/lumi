"""v0.38 LUMI-149 — Inner Voice: the editable three-voice think instruction replaces REASONING_DIRECTIVE.

No paid calls — a MockLLMClient records the system prompt each turn so we can assert which think
directive rode in it.
"""
from __future__ import annotations

from core.agent import Core, build_core
from core.config import DEFAULT_INNER_VOICE_PATH, Config
from core.llm import MockLLMClient
from core.prompt import REASONING_DIRECTIVE, load_inner_voice
from state.local_store import JsonRepository

_STATE = {"reply": "Привіт!", "emotion": "joy", "intensity": 0.8}


def _core(tmp_path, llm, **kw) -> Core:
    return Core(
        llm=llm, repository=JsonRepository(tmp_path / "s.json"),
        canon="Ти — Лілі.", model="m",
        mood_enabled=False, biorhythms_enabled=False, cycle_enabled=False, **kw,
    )


def _system_of_last_turn(core: Core, llm: MockLLMClient) -> str:
    core.reply("привіт", core.start_session())
    return str(llm.calls[-1]["system"])


# --- the loader ------------------------------------------------------------------------------------
def test_load_inner_voice_reads_file(tmp_path):
    p = tmp_path / "iv.md"
    p.write_text("ІМПУЛЬС / ТВЕРЕЗІСТЬ / СТАНДАРТ", encoding="utf-8")
    assert load_inner_voice(p) == "ІМПУЛЬС / ТВЕРЕЗІСТЬ / СТАНДАРТ"


def test_load_inner_voice_missing_or_empty_returns_none(tmp_path):
    assert load_inner_voice(tmp_path / "nope.md") is None
    empty = tmp_path / "empty.md"
    empty.write_text("   \n", encoding="utf-8")
    assert load_inner_voice(empty) is None  # graceful → caller falls back to REASONING_DIRECTIVE


# --- the directive in the system prompt ------------------------------------------------------------
def test_default_uses_reasoning_directive(tmp_path):
    llm = MockLLMClient(states=dict(_STATE))
    system = _system_of_last_turn(_core(tmp_path, llm), llm)
    assert REASONING_DIRECTIVE in system  # off → today's generic directive, byte-identical


def test_inner_voice_replaces_the_generic_directive(tmp_path):
    voice = "Це торг трьох голосів: ІМПУЛЬС, ТВЕРЕЗІСТЬ, СТАНДАРТ. Загорни міркування у <think>…</think>."
    llm = MockLLMClient(states=dict(_STATE))
    system = _system_of_last_turn(_core(tmp_path, llm, reasoning_directive=voice), llm)
    assert voice in system and REASONING_DIRECTIVE not in system  # replaced, not appended


def test_reply_is_one_model_call(tmp_path):
    # one-call invariant: the visible reply is a single model call (no second think-call); housekeeping
    # (mood/summary/facts) is off here and runs separately anyway.
    llm = MockLLMClient(states=dict(_STATE))
    core = _core(tmp_path, llm)
    core.reply("привіт", core.start_session())
    assert len(llm.calls) == 1


# --- build_core wiring -----------------------------------------------------------------------------
def test_build_core_loads_inner_voice_when_on(tmp_path):
    vf = tmp_path / "iv.md"
    vf.write_text("ТРИ ГОЛОСИ. Загорни міркування у <think>…</think>.", encoding="utf-8")
    llm = MockLLMClient(states=dict(_STATE))
    cfg = Config(store_path=tmp_path / "s.json", inner_voice=True, inner_voice_path=vf)
    core = build_core(config=cfg, llm=llm, repository=JsonRepository(tmp_path / "s.json"))
    system = _system_of_last_turn(core, llm)
    assert "ТРИ ГОЛОСИ" in system and REASONING_DIRECTIVE not in system


def test_build_core_off_uses_reasoning_directive(tmp_path):
    llm = MockLLMClient(states=dict(_STATE))
    cfg = Config(store_path=tmp_path / "s.json")  # inner_voice off (default)
    core = build_core(config=cfg, llm=llm, repository=JsonRepository(tmp_path / "s.json"))
    assert REASONING_DIRECTIVE in _system_of_last_turn(core, llm)


def test_build_core_on_but_missing_file_degrades_to_directive(tmp_path):
    llm = MockLLMClient(states=dict(_STATE))
    cfg = Config(store_path=tmp_path / "s.json", inner_voice=True, inner_voice_path=tmp_path / "nope.md")
    core = build_core(config=cfg, llm=llm, repository=JsonRepository(tmp_path / "s.json"))
    assert REASONING_DIRECTIVE in _system_of_last_turn(core, llm)  # graceful, no crash


# --- the shipped file is a valid three-voice instruction -------------------------------------------
def test_shipped_inner_voice_file_is_authored_three_voice():
    text = load_inner_voice(DEFAULT_INNER_VOICE_PATH)
    assert text is not None
    # v2: the three voices are intent-owning movement vectors (him / her / the shared weave).
    for voice in ("ЦІКАВІСТЬ", "НОРОВ", "ПАМ'ЯТЬ"):
        assert voice in text  # the three voices are present
    assert "<think>" in text  # keeps the wrap mechanism so split_reasoning still lifts the monologue
    assert "<intent>" in text  # the arbiter still tags the conversation move (v1.1)
    assert "компетентн" in text.lower()  # the never-competence invariant is authored in

"""v0.4 LUMI-021: the ambient now/here block in the system prompt."""

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from core.prompt import build_system_prompt
from core.worldcontext import WorldContext, ambient_line
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 6, 7, 14, 30, tzinfo=UTC))


def _core(tmp_path):
    return Core(
        llm=MockLLMClient("ok"),
        repository=JsonRepository(tmp_path / "s.json"),
        canon="Ти — Лілі.",
        model="m",
        clock=_CLK,
    )


def test_ambient_block_appears_when_a_world_is_set(tmp_path):
    core = _core(tmp_path)
    core.set_world_context(
        WorldContext(location="Львів", weather="15°C, ясно", news=("Перша новина",))
    )
    core.reply("привіт", core.start_session())
    system = core.last_prompt["system"]
    assert "Зараз і тут" in system
    assert "Львів" in system and "15°C" in system and "Перша новина" in system
    assert "2026-06-07 14:30" in system  # the "now" is recomputed from the clock


def test_no_ambient_block_when_world_is_unset(tmp_path):
    core = _core(tmp_path)
    core.reply("привіт", core.start_session())
    assert "Зараз і тут" not in core.last_prompt["system"]


def test_ambient_line_recomputes_now_from_the_clock():
    line = ambient_line(WorldContext(location="Київ"), _CLK)
    assert "2026-06-07 14:30" in line and "Київ" in line
    assert ambient_line(None, _CLK) is None  # no snapshot → no block


def test_build_system_prompt_ambient_is_opt_in():
    assert build_system_prompt("CANON") == "CANON"
    assert "X-AMB" in build_system_prompt("CANON", ambient="X-AMB")

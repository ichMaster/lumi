"""Textual pilot smoke test for the TUI loop (LUMI-006).

Drives a turn through the real app against MockLLMClient (no paid call) and
asserts a model failure degrades to a readable line instead of crashing.
"""

from core.agent import Core
from core.llm import MockLLMClient
from state.local_store import JsonRepository
from tui.app import ERROR_LINE, LumiApp


def _core(tmp_path, llm):
    return Core(
        llm=llm,
        repository=JsonRepository(tmp_path / "store.json"),
        system_prompt="Ти — Лілі.",
        model="claude-haiku-4-5-20251001",
    )


async def _submit(pilot, app, text):
    app.query_one("#prompt").value = text
    await pilot.press("enter")
    for _ in range(50):  # let the off-thread reply land
        await pilot.pause()
        if len(app.transcript) >= 2:
            break


async def test_tui_drives_a_turn_against_mock_model(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("Привіт. Я Лілі.")))
    async with app.run_test() as pilot:
        await _submit(pilot, app, "привіт")
        assert any("Ти: привіт" in line for line in app.transcript)
        assert any("Лілі: Привіт. Я Лілі." in line for line in app.transcript)


async def test_empty_input_is_ignored(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("...")))
    async with app.run_test() as pilot:
        app.query_one("#prompt").value = "   "
        await pilot.press("enter")
        await pilot.pause()
        assert app.transcript == []


async def test_model_failure_degrades_to_a_readable_line(tmp_path):
    def boom(system, messages, model):
        raise RuntimeError("model down")

    app = LumiApp(_core(tmp_path, MockLLMClient(boom)))
    async with app.run_test() as pilot:
        await _submit(pilot, app, "привіт")
        assert any(ERROR_LINE in line for line in app.transcript)
        # The loop is still alive: the input is re-enabled and focused.
        assert app.query_one("#prompt").disabled is False

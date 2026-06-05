"""Textual pilot smoke test for the TUI loop (LUMI-006).

Drives a turn through the real app against MockLLMClient (no paid call) and
asserts a model failure degrades to a readable line instead of crashing.
"""

from rich.markdown import Markdown

from core.agent import Core
from core.llm import MockLLMClient
from state.local_store import JsonRepository
from tui.app import (
    ERROR_COLOR,
    ERROR_LINE,
    LILI_COLOR,
    LILI_LABEL,
    USER_COLOR,
    USER_LABEL,
    LumiApp,
)


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


def test_lili_reply_is_rendered_as_markdown():
    # Лілі writes Markdown; the body must render as Markdown, not literal text.
    label, body = LumiApp._markdown_block(LILI_LABEL, "це **жирно** і `код`", LILI_COLOR)
    assert isinstance(body, Markdown)
    assert body.markup == "це **жирно** і `код`"
    # The label keeps the speaker color.
    assert LILI_COLOR in str(label.style)


def test_speakers_have_distinct_colors():
    # Your lines and Лілі's must be styled differently so they read apart.
    assert USER_COLOR != LILI_COLOR
    user_line = LumiApp._styled(USER_LABEL, "привіт", USER_COLOR)
    lili_line = LumiApp._styled(LILI_LABEL, "вітаю", LILI_COLOR)
    user_styles = {str(span.style) for span in user_line.spans}
    lili_styles = {str(span.style) for span in lili_line.spans}
    assert any(USER_COLOR in s for s in user_styles)
    assert any(LILI_COLOR in s for s in lili_styles)
    assert user_styles.isdisjoint(lili_styles)
    assert ERROR_COLOR not in {USER_COLOR, LILI_COLOR}


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


async def test_ctrl_y_copies_lili_last_reply(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("Це **відповідь**.")))
    copied: list[str] = []
    async with app.run_test() as pilot:
        app.copy_to_clipboard = copied.append  # capture OSC-52 payload
        await _submit(pilot, app, "привіт")
        await pilot.press("ctrl+y")
        await pilot.pause()
        assert copied == ["Це **відповідь**."]


async def test_copy_all_copies_full_conversation(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("вітаю")))
    copied: list[str] = []
    async with app.run_test() as pilot:
        app.copy_to_clipboard = copied.append
        await _submit(pilot, app, "привіт")
        app.action_copy_all()
        assert copied and "Ти: привіт" in copied[0] and "Лілі: вітаю" in copied[0]


async def test_copy_reply_with_nothing_yet_does_not_copy(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("...")))
    copied: list[str] = []
    async with app.run_test() as pilot:
        app.copy_to_clipboard = copied.append
        app.action_copy_reply()  # no reply yet
        await pilot.pause()
        assert copied == []

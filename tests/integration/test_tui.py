"""Textual pilot smoke test for the TUI loop (LUMI-006).

Drives a turn through the real app against MockLLMClient (no paid call) and
asserts a model failure degrades to a readable line instead of crashing.
"""

from rich.markdown import Markdown
from textual.widgets import Static

from core.agent import Core
from core.llm import MockLLMClient
from state.local_store import JsonRepository
from tui.app import (
    ERROR_COLOR,
    ERROR_LINE,
    LILI_COLOR,
    LILI_LABEL,
    STATUS_BUSY,
    STATUS_READY,
    USER_COLOR,
    USER_LABEL,
    LumiApp,
)


def _core(tmp_path, llm):
    return Core(
        llm=llm,
        repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.",
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


async def test_thinking_is_shown_greyed_above_the_reply(tmp_path):
    llm = MockLLMClient("Привіт!", thinking="Подумаю, як відповісти тепло.")
    app = LumiApp(_core(tmp_path, llm))
    async with app.run_test() as pilot:
        await _submit(pilot, app, "привіт")
        joined = "\n".join(app.transcript)
        assert "💭 Подумаю, як відповісти тепло." in joined
        assert "Лілі: Привіт!" in joined
        # The thinking line comes before the reply.
        think_idx = next(i for i, ln in enumerate(app.transcript) if ln.startswith("💭"))
        reply_idx = next(i for i, ln in enumerate(app.transcript) if ln.startswith("Лілі:"))
        assert think_idx < reply_idx


async def test_no_thinking_line_when_absent(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("Привіт!")))  # no thinking
    async with app.run_test() as pilot:
        await _submit(pilot, app, "привіт")
        assert not any(line.startswith("💭") for line in app.transcript)


async def test_status_and_stats_are_separate_lines(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("Привіт!")))
    async with app.run_test() as pilot:
        # Both widgets are mounted.
        app.query_one("#status", Static)
        app.query_one("#stats", Static)
        # Status = technical state (model + online); stats = numbers (separate).
        assert "haiku" in app._status_text() and STATUS_READY in app._status_text()
        assert "total" not in app._status_text()  # totals live on the stats line
        await _submit(pilot, app, "привіт")
        assert "total 1 turns" in app._stats_text()  # one turn counted, on stats line


async def test_status_has_no_icons(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("Привіт!")))
    async with app.run_test() as pilot:
        await _submit(pilot, app, "привіт")
        for glyph in ("●", "◐", "⚠", "💭", "📊", "↑", "↓"):
            assert glyph not in app._status_text()
            assert glyph not in app._stats_text()
            assert glyph not in app._status_text(busy=STATUS_BUSY)


async def test_busy_is_a_technical_status_and_stats_stay_visible(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("Привіт!")))
    async with app.run_test() as pilot:
        await _submit(pilot, app, "привіт")
        # Busy state is the technical connection status (not persona "думає").
        assert STATUS_BUSY in app._status_text(busy=STATUS_BUSY)
        assert "думає" not in app._status_text(busy=STATUS_BUSY)
        # ...and the stats line still has data while busy.
        assert "total 1 turns" in app._stats_text()


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


async def test_ctrl_l_clears_screen_but_keeps_memory(tmp_path):
    repo = JsonRepository(tmp_path / "store.json")
    core = Core(
        llm=MockLLMClient("вітаю"),
        repository=repo,
        canon="Ти — Лілі.",
        model="m",
    )
    app = LumiApp(core)
    async with app.run_test() as pilot:
        await _submit(pilot, app, "привіт")
        assert app.transcript
        session_id = app._session.id
        await pilot.press("ctrl+l")
        await pilot.pause()
        assert app.transcript == []
        assert app._last_reply is None
        # Лілі still remembers — the persisted store kept the turn.
        assert len(repo.load_messages(session_id)) == 2


async def test_toggle_mouse_selection_flips_flag(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("...")))
    async with app.run_test() as pilot:
        assert app._mouse_selection is False
        app.action_toggle_mouse()
        assert app._mouse_selection is True
        app.action_toggle_mouse()
        assert app._mouse_selection is False
        await pilot.pause()

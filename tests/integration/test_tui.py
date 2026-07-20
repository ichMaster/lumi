"""Textual pilot smoke test for the TUI loop (LUMI-006).

Drives a turn through the real app against MockLLMClient (no paid call) and
asserts a model failure degrades to a readable line instead of crashing.
"""

import pytest
from rich.markdown import Markdown
from textual.css.query import NoMatches
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
    ChatInput,
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
    app.query_one("#prompt").text = text
    await pilot.press("enter")
    for _ in range(50):  # let the off-thread reply land
        await pilot.pause()
        if len(app.transcript) >= 2:
            break


async def test_tui_drives_a_turn_against_mock_model(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("Привіт. Я Лілі.")))
    async with app.run_test() as pilot:
        await _submit(pilot, app, "привіт")
        assert any("You: привіт" in line for line in app.transcript)
        assert any("Лілі 🙂: Привіт. Я Лілі." in line for line in app.transcript)


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
        app.query_one("#prompt").text = "   "
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


async def test_no_thinking_keeps_the_box_empty(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("Привіт!")))  # no thinking
    async with app.run_test() as pilot:
        await _submit(pilot, app, "привіт")
        assert app._thinking_shown is None  # the Thinking box stays empty
        assert "Лілі 🙂: Привіт!" in "\n".join(app.transcript)


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
        app._copy = copied.append  # capture clipboard payload (pbcopy + OSC-52)
        await _submit(pilot, app, "привіт")
        await pilot.press("ctrl+y")
        await pilot.pause()
        assert copied == ["Це **відповідь**."]


async def test_copy_all_copies_full_conversation(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("вітаю")))
    copied: list[str] = []
    async with app.run_test() as pilot:
        app._copy = copied.append
        await _submit(pilot, app, "привіт")
        app.action_copy_all()
        assert copied and "You: привіт" in copied[0] and "Лілі 🙂: вітаю" in copied[0]


async def test_copy_reply_with_nothing_yet_does_not_copy(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("...")))
    copied: list[str] = []
    async with app.run_test() as pilot:
        app._copy = copied.append
        app.action_copy_reply()  # no reply yet
        await pilot.pause()
        assert copied == []


async def test_new_session_starts_fresh_and_processes_previous(tmp_path):
    repo = JsonRepository(tmp_path / "store.json")
    core = Core(llm=MockLLMClient("ok"), repository=repo, canon="Ти — Лілі.", model="m")
    app = LumiApp(core)
    async with app.run_test() as pilot:
        await _submit(pilot, app, "привіт")
        first = app._session.id
        await _submit(pilot, app, "/new")
        # A new session is active, and the previous one was ended (processed).
        assert app._session.id != first
        assert repo.get_session(first).ended_at is not None
        # The screen is cleared — only the divider remains; prior lines are gone.
        assert any("new session" in line for line in app.transcript)
        assert not any("привіт" in line for line in app.transcript)


async def test_thinking_shows_in_the_box_not_the_chat(tmp_path):
    llm = MockLLMClient("привіт", thinking="Лілі обмірковує відповідь.")
    app = LumiApp(_core(tmp_path, llm))
    async with app.run_test() as pilot:
        await _submit(pilot, app, "як ти?")
        # The reasoning is in the Thinking box…
        assert app._thinking_shown == "Лілі обмірковує відповідь."
        # …and never in the chat transcript.
        assert not any("обмірковує" in line for line in app.transcript)


async def test_thinking_box_clears_when_a_turn_has_no_thinking(tmp_path):
    # First turn has thinking; the second doesn't → the box must empty.
    llm = MockLLMClient("ага", thinking="Перша думка.")
    app = LumiApp(_core(tmp_path, llm))
    async with app.run_test() as pilot:
        await _submit(pilot, app, "раз")
        assert app._thinking_shown == "Перша думка."
        llm._thinking_text = None  # next reply carries no thinking
        app.query_one("#prompt").text = "два"
        await pilot.press("enter")
        for _ in range(50):  # wait for the second reply to land (transcript → 4)
            await pilot.pause()
            if len(app.transcript) >= 4:
                break
        assert app._thinking_shown is None  # box emptied


async def test_openai_style_public_thinking_summary_populates_the_box(tmp_path):
    llm = MockLLMClient(states={
        "reply": "Привіт!",
        "emotion": "calm",
        "intensity": 0.5,
        "thinking_summary": "Зважила тон і відповіла коротко та тепло.",
    })
    llm._thinking = True  # OpenAI Responses path: the status says "thinking on"
    app = LumiApp(_core(tmp_path, llm))
    async with app.run_test() as pilot:
        await _submit(pilot, app, "привіт")
        assert app._thinking_shown == "Зважила тон і відповіла коротко та тепло."


async def test_think_show_off_hides_the_box(tmp_path):
    # v0.38 LUMI-150: LUMI_THINK_SHOW=off → the monologue is captured in the core but the TUI box stays empty.
    llm = MockLLMClient("ага", thinking="Прихована думка.")
    core = Core(llm=llm, repository=JsonRepository(tmp_path / "store.json"),
                canon="Ти — Лілі.", model="m", think_show="off")
    app = LumiApp(core)
    async with app.run_test() as pilot:
        await _submit(pilot, app, "раз")
        assert core.last_thinking == "Прихована думка."  # captured in the core (ephemeral)
        assert app._thinking_shown is None               # hidden in the TUI (off)


async def test_quit_summarizes_session_then_exits(tmp_path):
    repo = JsonRepository(tmp_path / "store.json")
    core = Core(llm=MockLLMClient("ok"), repository=repo, canon="Ти — Лілі.", model="m")
    app = LumiApp(core)
    async with app.run_test() as pilot:
        await _submit(pilot, app, "привіт")
        sid = app._session.id
        await app.action_quit()
        # The session was processed (summarized) before exit…
        assert repo.get_session(sid).ended_at is not None
        # …a system message announced it (in English)…
        assert any("Saving session before exit" in line for line in app.transcript)
        # …and it won't be re-processed on unmount.
        assert app._session is None


async def test_prompt_command_shows_last_turn_prompt(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("ok")))
    async with app.run_test() as pilot:
        await _submit(pilot, app, "привіт")
        app.query_one("#prompt").text = "/prompt"
        await pilot.press("enter")
        await pilot.pause()
        joined = "\n".join(app.transcript)
        assert "[SYSTEM]" in joined and "[MESSAGES]" in joined
        assert "Ти — Лілі." in joined  # the canon that was sent
        assert "] привіт" in joined  # the (timestamped, v0.4) user line


async def test_input_is_locked_while_busy(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("ok")))
    async with app.run_test() as pilot:
        app._input_buffer = False  # this test pins the classic locked-input mode (v1.2 buffer off)
        prompt = app.query_one("#prompt", ChatInput)
        assert prompt.disabled is False  # your turn → unlocked

        app._set_busy(True)  # a turn in flight → the box locks
        assert prompt.disabled is True
        # A submit that slips through the lock is ignored — no send, draft kept.
        prompt.text = "моя наступна думка"
        await pilot.press("enter")
        await pilot.pause()
        assert not any(line.startswith("You:") for line in app.transcript)

        app._set_busy(False)  # her reply done → unlocked, your turn again
        assert prompt.disabled is False


async def test_send_works_again_once_free(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("вітаю")))
    async with app.run_test() as pilot:
        # A normal turn flips busy True during the call, then back to False.
        await _submit(pilot, app, "привіт")
        assert app._busy is False
        assert any("Лілі 🙂: вітаю" in line for line in app.transcript)


async def test_multiline_input_enter_submits_shift_enter_newlines(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("ok")))
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", ChatInput)
        prompt.focus()
        prompt.text = "рядок1"
        await pilot.press("shift+enter")  # insert a newline, do not submit
        prompt.insert("рядок2")
        await pilot.pause()
        assert "\n" in prompt.text
        # Nothing submitted yet.
        assert not any(line.startswith("You:") for line in app.transcript)


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


async def test_status_line_shows_the_emotion(tmp_path):
    llm = MockLLMClient(states={"reply": "Радо!", "emotion": "joy", "intensity": 0.8})
    app = LumiApp(_core(tmp_path, llm))
    async with app.run_test() as pilot:
        await _submit(pilot, app, "привіт")
        assert "joy 0.8" in app._status_text()  # v0.3: current emotion in the status line


async def test_status_line_shows_the_intent_when_set(tmp_path):
    # v1.1: the chosen conversation move appears in the status line; absent when there is none.
    llm = MockLLMClient(states={"reply": "ок", "emotion": "calm", "intensity": 0.5, "intent": "deepen"})
    core = Core(llm=llm, repository=JsonRepository(tmp_path / "store.json"),
                canon="Ти — Лілі.", model="m", intent_enabled=True)
    app = LumiApp(core)
    async with app.run_test() as pilot:
        assert "intent:" not in app._status_text()   # nothing yet
        await _submit(pilot, app, "привіт")
        assert "intent:deepen" in app._status_text()


async def test_status_line_omits_intent_when_disabled(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("ok")))  # intent off by default
    async with app.run_test() as pilot:
        await _submit(pilot, app, "привіт")
        assert "intent:" not in app._status_text()


async def test_turn_routes_state_through_the_renderer(tmp_path):
    llm = MockLLMClient(states={"reply": "ок", "emotion": "playful", "intensity": 0.6})
    app = LumiApp(_core(tmp_path, llm))
    async with app.run_test() as pilot:
        await _submit(pilot, app, "привіт")
        # The validated state went through the LogRenderer (IEmotionRenderer), not raw output.
        assert app._renderer.session_id == app._session.id
        assert app._renderer.turn == 1


async def test_missing_reply_degrades_to_a_readable_error(tmp_path):
    llm = MockLLMClient(states={"emotion": "joy", "intensity": 0.5})  # no reply → EmotionError
    app = LumiApp(_core(tmp_path, llm))
    async with app.run_test() as pilot:
        await _submit(pilot, app, "привіт")
        assert any(ERROR_LINE in line for line in app.transcript)  # no crash
        assert app._connected is False


async def test_status_shows_thinking_off_by_default(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("ok")))
    async with app.run_test():
        assert "thinking:off" in app._status_text()  # mock has no thinking enabled


async def test_status_shows_thinking_on_when_enabled(tmp_path):
    llm = MockLLMClient("ok")
    llm._thinking = True  # simulate LUMI_THINKING=on
    app = LumiApp(_core(tmp_path, llm))
    async with app.run_test():
        assert "thinking:on" in app._status_text()


async def test_idle_nudge_runs_a_hidden_turn(tmp_path):
    from datetime import UTC, datetime, timedelta

    from core.clock import fixed_clock

    now = datetime(2026, 6, 7, 14, 0, tzinfo=UTC)
    core = Core(
        llm=MockLLMClient("Привіт, я сама почала."),
        repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.",
        model="m",
        clock=fixed_clock(now),
    )
    app = LumiApp(core)
    async with app.run_test() as pilot:
        # Arm the nudge directly and make it idle.
        app._nudge_enabled = True
        app._nudges = ["ти тут?"]
        app._idle_seconds = 60
        # v0.12: the nudge gates on the later of real-input idle AND its own last fire.
        app._last_activity = now - timedelta(seconds=120)
        app._last_nudge_ts = now - timedelta(seconds=120)
        app._maybe_nudge()
        for _ in range(50):
            await pilot.pause()
            if app.transcript:
                break
        # Лілі's reply is shown…
        assert any("Лілі 🙂: Привіт, я сама почала." in line for line in app.transcript)
        # …but the hidden nudge line is NOT in the displayed transcript.
        assert not any("ти тут?" in line for line in app.transcript)
        assert not any(line.startswith("You:") for line in app.transcript)


async def test_idle_nudge_off_does_nothing(tmp_path):
    from datetime import UTC, datetime, timedelta

    from core.clock import fixed_clock

    now = datetime(2026, 6, 7, 14, 0, tzinfo=UTC)
    core = Core(
        llm=MockLLMClient("ok"),
        repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.",
        model="m",
        clock=fixed_clock(now),
    )
    app = LumiApp(core)
    async with app.run_test() as pilot:
        app._nudge_enabled = False  # off (the default)
        app._nudges = ["ти тут?"]
        app._last_activity = now - timedelta(seconds=999)
        app._maybe_nudge()
        await pilot.pause()
        assert app.transcript == []  # nothing fired


async def test_emoji_shows_next_to_lili_reply(tmp_path):
    from core.llm import MockLLMClient as _Mock

    # joy 0.9 → high band → 😄✨✨ next to her name.
    llm = _Mock(states={"reply": "Ура!", "emotion": "joy", "intensity": 0.9})
    app = LumiApp(_core(tmp_path, llm))
    async with app.run_test() as pilot:
        await _submit(pilot, app, "вітаю")
        line = next(ln for ln in app.transcript if "Ура!" in ln)
        assert line.startswith("Лілі 😄✨✨:")  # emoji + high-intensity emphasis


async def test_emoji_low_intensity_is_the_plain_face(tmp_path):
    from core.llm import MockLLMClient as _Mock

    llm = _Mock(states={"reply": "ага", "emotion": "doubt", "intensity": 0.1})
    app = LumiApp(_core(tmp_path, llm))
    async with app.run_test() as pilot:
        await _submit(pilot, app, "точно?")
        assert any(ln.startswith("Лілі 😕:") for ln in app.transcript)  # plain low glyph


async def test_mood_command_shows_the_resolution(tmp_path):
    from datetime import UTC, datetime

    from core.clock import fixed_clock

    reading = "День легкий.\n\nРЕЗОЛЮЦІЯ:\nХотітиметься тиші й творчості. Настрій спокійний."
    llm = MockLLMClient(replies=reading, states={"reply": "ок", "emotion": "calm", "intensity": 0.5})
    core = Core(
        llm=llm,
        repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.",
        model="m",
        clock=fixed_clock(datetime(2026, 6, 7, 9, 0, tzinfo=UTC)),
        natal="Сонце 15° Риб",
        mood_enabled=True,
    )
    app = LumiApp(core)
    async with app.run_test() as pilot:
        for _ in range(50):  # startup triggers the mood off-thread
            await pilot.pause()
            if core.mood:
                break
        app.query_one("#prompt").text = "/mood"
        await pilot.press("enter")
        await pilot.pause()
        joined = "\n".join(app.transcript)
        assert "Настрій Лілі сьогодні" in joined and "Хотітиметься тиші" in joined


async def test_mood_command_pending_when_no_mood(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("ok")))  # no natal → no mood
    async with app.run_test() as pilot:
        app.query_one("#prompt").text = "/mood"
        await pilot.press("enter")
        await pilot.pause()
        assert any("ще не визначила настрій" in line for line in app.transcript)


class _SpySound:
    """A stand-in for SoundPlayer that counts plays (no audio)."""

    def __init__(self):
        self.sends = 0
        self.receives = 0

    def ensure(self):
        return True

    def send(self):
        self.sends += 1

    def receive(self):
        self.receives += 1


async def test_sound_on_send_and_receive_but_never_on_the_nudge(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("ок")))
    async with app.run_test() as pilot:
        spy = _SpySound()
        app._sound = spy
        app._sound_on = True

        await _submit(pilot, app, "привіт")  # a real turn → one send + one receive
        assert (spy.sends, spy.receives) == (1, 1)

        await app._run_turn("щось своє", hidden=True)  # the idle nudge — hidden, silent
        assert (spy.sends, spy.receives) == (1, 1)  # unchanged


async def test_ctrl_s_toggles_sound_and_shows_in_status(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("ок")))
    async with app.run_test() as pilot:
        app._sound = _SpySound()  # ensure() True so the toggle can turn on
        assert app._sound_on is False
        assert "sound:off" in app._status_text()

        await pilot.press("ctrl+s")
        assert app._sound_on is True
        assert "sound:on" in app._status_text()

        await pilot.press("ctrl+s")
        assert app._sound_on is False
        assert "sound:off" in app._status_text()


# --- v1.2 LUMI-180: decouple the input widget from the busy mutex --------------------------------


async def test_input_buffer_keeps_the_widget_editable_during_a_turn(tmp_path):
    # With the buffer on, _busy stays the model mutex but the input box is NOT disabled.
    app = LumiApp(_core(tmp_path, MockLLMClient("Привіт!")))
    async with app.run_test():
        app._input_buffer = True
        app._set_busy(True)
        assert app._busy is True
        assert app.query_one("#prompt", ChatInput).disabled is False  # editable while busy
        app._set_busy(False)


async def test_without_buffer_the_widget_locks_during_a_turn(tmp_path):
    # Off (default) → today's behavior: the box is disabled for the turn, re-enabled after.
    app = LumiApp(_core(tmp_path, MockLLMClient("Привіт!")))
    async with app.run_test():
        app._input_buffer = False
        app._set_busy(True)
        assert app.query_one("#prompt", ChatInput).disabled is True  # locked
        app._set_busy(False)
        assert app.query_one("#prompt", ChatInput).disabled is False


async def test_handle_input_line_routes_a_command_without_a_turn(tmp_path):
    # The extracted dispatch handles commands exactly as the live submit did — no model turn
    # (the mock reply never appears; the command just shows its own output).
    app = LumiApp(_core(tmp_path, MockLLMClient("Привіт!")))
    async with app.run_test():
        await app._handle_input_line("/memory")
        assert not any("Привіт!" in line for line in app.transcript)  # no model turn ran


# --- v1.2 LUMI-181: enqueue + immediate echo while busy ------------------------------------------


async def test_submit_while_busy_enqueues_and_echoes_without_a_turn(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("Привіт!")))
    async with app.run_test() as pilot:
        app._input_buffer = True
        app._set_busy(True)  # pretend Лілі is mid-reply
        app.query_one("#prompt", ChatInput).text = "а ще?"
        await pilot.press("enter")
        await pilot.pause()
        assert list(app._input_queue) == ["а ще?"]  # queued
        assert any("а ще?" in line for line in app.transcript)  # echoed now
        assert app.query_one("#prompt", ChatInput).text == ""  # box cleared
        assert app._busy is True  # no new turn started (still the same turn)


async def test_rapid_submits_while_busy_enqueue_in_order(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("Привіт!")))
    async with app.run_test() as pilot:
        app._input_buffer = True
        app._set_busy(True)
        for msg in ("перше", "друге", "третє"):
            app.query_one("#prompt", ChatInput).text = msg
            await pilot.press("enter")
            await pilot.pause()
        assert list(app._input_queue) == ["перше", "друге", "третє"]  # FIFO


async def test_queued_command_is_not_echoed_as_a_user_row(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("Привіт!")))
    async with app.run_test() as pilot:
        app._input_buffer = True
        app._set_busy(True)
        app.query_one("#prompt", ChatInput).text = "/memory"
        await pilot.press("enter")
        await pilot.pause()
        assert list(app._input_queue) == ["/memory"]  # queued
        assert not any("/memory" in line for line in app.transcript)  # a command shows no user row


# --- v1.2 LUMI-182: drain — one turn per queued line, FIFO ---------------------------------------


async def test_queued_messages_each_get_their_own_reply(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("ok")))
    async with app.run_test() as pilot:
        app._input_buffer = True
        app._set_busy(True)  # pretend Лілі is mid-reply
        for msg in ("перше", "друге"):
            app.query_one("#prompt", ChatInput).text = msg
            await pilot.press("enter")
            await pilot.pause()
        assert list(app._input_queue) == ["перше", "друге"]
        # the current turn finishes → drain (as _run_turn's finally does)
        app._set_busy(False)
        app._drain_input_queue()
        for _ in range(300):
            await pilot.pause()
            if not app._input_queue and not app._busy and not app._draining:
                break
        lili = [line for line in app.transcript if line.startswith("Лілі")]
        assert len(lili) == 2  # one reply per queued message — NEVER merged into one
        assert not app._input_queue  # fully drained


async def test_queued_command_does_not_stall_the_drain(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("ok")))
    async with app.run_test() as pilot:
        app._input_buffer = True
        app._input_queue.extend(["/memory", "далі"])  # a command, then a chat line
        app._drain_input_queue()
        for _ in range(300):
            await pilot.pause()
            if not app._input_queue and not app._busy and not app._draining:
                break
        assert not app._input_queue  # the command didn't stall the drain
        assert any(line.startswith("Лілі") for line in app.transcript)  # the chat line got answered


async def test_drained_chat_line_shows_no_duplicate_user_row(tmp_path):
    # A queued line's user row was echoed on submit; the drained turn must not repeat it.
    app = LumiApp(_core(tmp_path, MockLLMClient("ok")))
    async with app.run_test() as pilot:
        app._input_buffer = True
        app._set_busy(True)
        app.query_one("#prompt", ChatInput).text = "привіт"
        await pilot.press("enter")
        await pilot.pause()
        app._set_busy(False)
        app._drain_input_queue()
        for _ in range(300):
            await pilot.pause()
            if not app._input_queue and not app._busy and not app._draining:
                break
        user_rows = [line for line in app.transcript if "You: привіт" in line]
        assert len(user_rows) == 1  # shown once (at submit), not again on the drained turn


# --- v1.2 LUMI-183: proactive guards, pending-count surfacing, off-pin ---------------------------


async def test_status_shows_the_pending_count(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("ok")))
    async with app.run_test():
        app._input_queue.extend(["a", "b"])
        assert "⋯2" in app._status_text()
        app._input_queue.clear()
        assert "⋯" not in app._status_text()


async def test_proactive_nudge_is_skipped_while_the_queue_is_nonempty(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("opener")))
    async with app.run_test() as pilot:
        app._nudge_enabled = True
        app._nudges = ["Гей, ти тут?"]
        app._input_queue.append("щось у черзі")  # pending input
        app._maybe_nudge()  # a proactive tick while messages wait
        await pilot.pause()
        # nothing fired — the nudge did not cut ahead of the queue
        assert not any("opener" in line or "Гей" in line for line in app.transcript)


async def test_off_the_queue_is_never_used_and_submit_while_busy_drops(tmp_path):
    # The off-pin: with the buffer off, the box locks during a turn and a stray submit is dropped
    # (no queue, no echo, no pending marker) — byte-identical to pre-v1.2.
    app = LumiApp(_core(tmp_path, MockLLMClient("ok")))
    async with app.run_test() as pilot:
        app._input_buffer = False
        app._set_busy(True)
        assert app.query_one("#prompt", ChatInput).disabled is True  # locked
        app.query_one("#prompt", ChatInput).text = "поки зайнято"
        await pilot.press("enter")
        await pilot.pause()
        assert list(app._input_queue) == []  # no queue used
        assert not any("поки зайнято" in line for line in app.transcript)  # not echoed
        assert "⋯" not in app._status_text()  # no pending marker
        app._set_busy(False)


# --- v1.2 fix: the input holds focus through a turn (so typing shows) ----------------------------


async def test_input_holds_focus_during_a_turn_with_buffer(tmp_path):
    import threading

    release = threading.Event()

    def slow(system, messages, model):
        release.wait(3)  # block the reply so we can inspect focus mid-turn
        return "ok"

    app = LumiApp(_core(tmp_path, MockLLMClient(slow)))
    async with app.run_test() as pilot:
        app._input_buffer = True
        app.run_worker(app._run_turn("привіт", mirror_input=True), exclusive=False)
        for _ in range(80):  # let the turn start (busy True, reply blocked in the thread)
            await pilot.pause()
            if app._busy:
                break
        try:
            box = app.query_one("#prompt", ChatInput)
            assert box.has_focus  # focus returned to the input → keystrokes land in the box
            await pilot.press("т", "е", "с", "т")
            await pilot.pause()
            assert box.text == "тест"  # typing shows while she answers
        finally:
            release.set()  # let the blocked reply finish before the screen tears down
            for _ in range(80):
                await pilot.pause()
                if not app._busy:
                    break


# --- v1.4 LUMI-189: TUI incremental render -------------------------------------------------------


def _core_stream(tmp_path, llm):
    return Core(
        llm=llm,
        repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.",
        model="m",
        stream=True,
    )


async def test_grow_stream_think_updates_the_box(tmp_path):
    app = LumiApp(_core_stream(tmp_path, MockLLMClient("ок")))
    async with app.run_test():
        app._grow_stream_think("зважую ")
        app._grow_stream_think("слова")
        assert app._stream_think_buf == "зважую слова"


async def test_streamed_reply_appears_once_in_the_conversation(tmp_path):
    # The v1.4 fix: the streamed reply grows in place inside the chat and STAYS — it appears exactly
    # once (no separate live box below + a second committed copy).
    llm = MockLLMClient(states={"reply": "Привіт, як ти?", "emotion": "joy", "intensity": 0.7},
                        stream_chunk=3)
    app = LumiApp(_core_stream(tmp_path, llm))
    async with app.run_test() as pilot:
        await _submit(pilot, app, "привіт")
        lili_lines = [ln for ln in app.transcript if "Привіт, як ти?" in ln]
        assert len(lili_lines) == 1                       # exactly ONE copy, not two
        assert lili_lines[0].startswith("Лілі")
        assert app._stream_body is None                   # finalized — the grown widget became the reply
        assert app._stream_buf == ""
        with pytest.raises(NoMatches):                    # the old separate #live box is gone
            app.query_one("#live")


async def test_streamed_turn_failure_discards_the_reply_widget(tmp_path):
    # A streamed turn that fails must not leave an empty "Лілі:" block dangling in the flow.
    def boom(system, messages, model):
        raise RuntimeError("model down")

    app = LumiApp(_core_stream(tmp_path, MockLLMClient(boom)))
    async with app.run_test() as pilot:
        await _submit(pilot, app, "привіт")
        assert any(ERROR_LINE in ln for ln in app.transcript)   # readable error, no crash
        assert app._stream_body is None                         # the mounted widget was discarded
        assert app.query_one("#prompt").disabled is False       # loop still alive


async def test_streaming_off_renders_reply_once(tmp_path):
    # Off-pin: default core (stream off) → the blocking render; the reply still appears exactly once.
    app = LumiApp(_core(tmp_path, MockLLMClient("добре")))
    async with app.run_test() as pilot:
        await _submit(pilot, app, "привіт")
        lili_lines = [ln for ln in app.transcript if "добре" in ln and ln.startswith("Лілі")]
        assert len(lili_lines) == 1
        assert app._stream_body is None                   # no streaming widget was used


async def test_stats_shows_ttft_first_indicator_when_streaming(tmp_path):
    # v1.4: the stats bar gains a "first" (time-to-first-symbol) indicator beside the response time.
    llm = MockLLMClient(states={"reply": "Привіт світ", "emotion": "joy", "intensity": 0.6}, stream_chunk=3)
    app = LumiApp(_core_stream(tmp_path, llm))
    async with app.run_test() as pilot:
        assert "first" not in app._stats_text()          # nothing streamed yet
        await _submit(pilot, app, "привіт")
        assert "first" in app._stats_text()              # TTFT indicator appears after a streamed turn


async def test_stats_has_no_ttft_indicator_without_streaming(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("добре")))  # stream off (default)
    async with app.run_test() as pilot:
        await _submit(pilot, app, "привіт")
        assert "first" not in app._stats_text()          # blocking turn → no first-symbol indicator


async def test_streamed_think_routes_to_box_not_chat(tmp_path):
    # <think> in the streamed reply goes to the Thinking box; the chat shows only the prose.
    llm = MockLLMClient(states={"reply": "<think>міркую тихо</think>Готово, друже", "emotion": "calm",
                                "intensity": 0.5})
    app = LumiApp(_core_stream(tmp_path, llm))
    async with app.run_test() as pilot:
        await _submit(pilot, app, "привіт")
        assert app._thinking_shown == "міркую тихо"
        joined = "\n".join(app.transcript)
        assert "Готово, друже" in joined
        assert "міркую тихо" not in joined and "<think>" not in joined


async def test_typing_shows_while_a_submitted_turn_runs(tmp_path):
    # The real regression: a submitted turn must run as a worker so the app's message pump keeps
    # dispatching keystrokes — an inline `await` in on_chat_input_submitted would freeze typing.
    import threading

    release = threading.Event()

    def slow(system, messages, model):
        release.wait(3)
        return "ok"

    app = LumiApp(_core(tmp_path, MockLLMClient(slow)))
    async with app.run_test() as pilot:
        app._input_buffer = True
        app.query_one("#prompt", ChatInput).text = "привіт"
        await pilot.press("enter")  # submit via the real path
        for _ in range(80):
            await pilot.pause()
            if app._busy:
                break
        try:
            await pilot.press("щ", "е")  # type while the reply is blocked
            await pilot.pause()
            # the pump dispatched the keys → the box updates (was frozen with an inline await)
            assert app.query_one("#prompt", ChatInput).text == "ще"
        finally:
            release.set()
            for _ in range(80):
                await pilot.pause()
                if not app._busy:
                    break

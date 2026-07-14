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

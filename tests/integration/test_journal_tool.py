"""v0.28 LUMI-111 — the journal tools wired into Core.reply (mock model + injected MoodState).

A full turn: the model scripts journal_write; the wiring composes the CODE-OWNED stamp from the injected
MoodState + biorhythms + the fixed clock, writes the dated file, and the {reply, emotion, intensity}
contract validates. /journal read/list go through Core helpers. No paid calls (mood is injected, not called).
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from core.agent import Core
from core.biorhythm import biorhythms
from core.clock import fixed_clock
from core.emotion import EmotionState
from core.llm import MockLLMClient
from core.mood import MoodState
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 6, 18, 21, 30, tzinfo=UTC))
_STATE = {"reply": "Записала собі сьогоднішній день.", "emotion": "tender", "intensity": 0.6}
_PROSE = "Весь день був з-під води. Тонка шкіра, але навіть на дні від тепла."


def _core(tmp_path, llm, *, journal=False, user="owner", with_mood=True) -> Core:
    core = Core(
        llm=llm, repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.", model="m", clock=_CLK, user_id=user,
        mood_enabled=False, closeness_enabled=False, thoughts_enabled=False,
        tool_max_steps=6, journal_enabled=journal, files_dir=tmp_path / "files",
    )
    if with_mood:  # mood is OFF, so _ensure_mood is a no-op and these injected values persist
        core._mood = MoodState(
            date="2026-06-18", resolution="тонка шкіра сьогодні; хочеться тиші",
            reading="Двадцять четвертий день циклу — відплив; те, що в інші дні відскакує, заходить глибоко.",
            theme=None)
        core._biorhythms = biorhythms(date(1990, 1, 1), date(2026, 6, 18))
    return core


def test_turn_writes_journal_with_code_owned_header(tmp_path):
    mock = MockLLMClient(states=_STATE, tool_script=[("journal_write", {"text": _PROSE})])
    core = _core(tmp_path, mock, journal=True)
    state = core.reply("запиши сьогоднішній день у щоденник", core.start_session())

    assert isinstance(state, EmotionState) and state.emotion.value == "tender"
    assert [c[0] for c in mock.tool_calls] == ["journal_write"]
    body = (tmp_path / "files" / "owner" / "journal" / "2026-06-18.md").read_text(encoding="utf-8")
    assert body.startswith("# 2026-06-18\n\n")
    assert "**Настрій:** тонка шкіра сьогодні" in body          # code-owned mood (from the injected MoodState)
    assert "**Біоритми:**" in body and "емоційний" in body      # code-owned biorhythms
    assert "**Прогноз:** Двадцять четвертий день" in body       # code-owned forecast (the reading)
    assert _PROSE in body                                       # her prose


def test_journal_read_and_list_via_core(tmp_path):
    mock = MockLLMClient(states=_STATE, tool_script=[("journal_write", {"text": _PROSE})])
    core = _core(tmp_path, mock, journal=True)
    core.reply("запиши день", core.start_session())
    assert "# 2026-06-18" in core.journal_read() and _PROSE in core.journal_read()
    assert "2026-06-18" in core.journal_list()


def test_no_journal_tools_when_off(tmp_path):
    mock = MockLLMClient(states=_STATE, tool_script=[("journal_write", {"text": "x"})])
    core = _core(tmp_path, mock, journal=False)
    core.reply("щоденник", core.start_session())
    assert mock.tool_calls == []                                # off → no journal tools offered
    assert core.journal_read() == "journal off"


def test_journal_directive_in_prompt_when_on(tmp_path):
    mock = MockLLMClient(states=_STATE, tool_script=[("journal_write", {"text": _PROSE})])
    core = _core(tmp_path, mock, journal=True)
    core.reply("запиши", core.start_session())
    assert "щоденник" in core.last_prompt["system"]             # the authored journal line rides the prompt


def test_stamp_degrades_when_mood_off(tmp_path):
    # No injected mood/biorhythms → the stamp omits those lines, the entry still writes (prose only).
    mock = MockLLMClient(states=_STATE, tool_script=[("journal_write", {"text": _PROSE})])
    core = _core(tmp_path, mock, journal=True, with_mood=False)
    core.reply("запиши", core.start_session())
    body = (tmp_path / "files" / "owner" / "journal" / "2026-06-18.md").read_text(encoding="utf-8")
    assert body.startswith("# 2026-06-18\n\n") and _PROSE in body and "**Настрій:**" not in body

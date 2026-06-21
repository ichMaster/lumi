"""v0.33 — %journal (the journal directive in the THINK path) actually calls journal_write.

The think-path tool-loop offers the journal tools, and the directive's ``tool_hint`` makes her USE
journal_write (not just muse a short thought) — so the day review reaches the dedicated diary with the
code-owned stamp, while the terminal thought is still recorded in the stream. Mock model — no paid calls.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from core.agent import Core
from core.biorhythm import biorhythms
from core.clock import fixed_clock
from core.llm import MockLLMClient
from core.mood import MoodState
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 6, 21, 22, 10, tzinfo=UTC))
_PROSE = "Сьогодні я цілий день ловила слово для дна, а воно дихало під ребрами."


def _core(tmp_path, llm) -> Core:
    core = Core(
        llm=llm, repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.", model="m", clock=_CLK, user_id="owner",
        mood_enabled=False, closeness_enabled=False,
        thoughts_enabled=True, thought_tools_enabled=True, thought_journal=True,
        journal_enabled=True, journal_dir=tmp_path / "journal",
    )
    # mood is OFF, so these injected values persist and feed the code-owned stamp
    core._mood = MoodState(date="2026-06-21", resolution="тонка шкіра; хочеться тиші",
                           reading="Відплив — заходить глибоко.", theme=None)
    core._biorhythms = biorhythms(date(1990, 1, 1), date(2026, 6, 21))
    return core


def test_journal_directive_writes_the_diary_via_journal_write(tmp_path):
    mock = MockLLMClient("Записала собі цей день.\nЕМОЦІЯ: tender",
                         tool_script=[("journal_write", {"text": _PROSE})])
    core = _core(tmp_path, mock)
    thought = core.think("journal", session=core.start_session())
    # the tool actually ran in the think loop → the dated diary file exists with the code-owned stamp
    assert [c[0] for c in mock.tool_calls] == ["journal_write"]
    body = (tmp_path / "journal" / "owner" / "2026-06-21.md").read_text(encoding="utf-8")
    assert body.startswith("# 2026-06-21\n\n") and _PROSE in body
    assert "**Настрій:** тонка шкіра" in body            # code-owned stamp (matches /mood + /biorhythm)
    assert "**Біоритми:**" in body
    # the terminal thought is still recorded in the stream — the short reflection, not the review
    assert thought is not None and thought.kind == "journal" and thought.text == "Записала собі цей день."


def test_journal_directive_via_run_directive(tmp_path):
    mock = MockLLMClient("День записано.\nЕМОЦІЯ: calm",
                         tool_script=[("journal_write", {"text": _PROSE})])
    core = _core(tmp_path, mock)
    out = core.run_directive("%journal", core.start_session())
    assert out.is_directive and out.thought is not None
    assert (tmp_path / "journal" / "owner" / "2026-06-21.md").is_file()


def test_journal_offered_the_journal_tools_in_the_think_loop(tmp_path):
    from core.thoughts import REGISTRY
    mock = MockLLMClient("День записано.\nЕМОЦІЯ: calm",
                         tool_script=[("journal_write", {"text": _PROSE})])
    core = _core(tmp_path, mock)
    tools, _ = core._thought_tools(REGISTRY["journal"])
    names = {t["name"] for t in (tools or [])}
    assert {"journal_write", "journal_read", "journal_list"} <= names

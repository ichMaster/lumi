"""Unit tests for the v0.8 hormonal (menstrual) cycle — phased, deterministic."""

from datetime import UTC, date, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.cycle import format_cycle, menstrual_phase, parse_cycle_anchor
from core.llm import MockLLMClient
from core.mood import mood_request
from state.local_store import JsonRepository

_ANCHOR = date(2026, 5, 1)  # a "day 1"
_NATAL = (
    "Народження: 05.03.2002, 10:15, Львів.\n"
    "Цикл: день 1 — 25.05.2026, довжина 28 днів.\n"
    "Сонце 15° Риб."
)
_READING = "День.\n\nРЕЗОЛЮЦІЯ:\nстан."
_DAY = fixed_clock(datetime(2026, 6, 8, 9, 0, tzinfo=UTC))


def _on(n):  # the date that is cycle-day n for _ANCHOR
    return date.fromordinal(_ANCHOR.toordinal() + (n - 1))


# --- the phase model ------------------------------------------------------
def test_phases_by_day_for_a_28_day_cycle():
    cases = {1: "менструація", 9: "фолікулярна", 14: "овуляція", 19: "лютеїнова", 26: "ПМС"}
    for day, phase in cases.items():
        p = menstrual_phase(_ANCHOR, _on(day))
        assert p.day == day and p.phase == phase, (day, p.phase)


def test_cycle_repeats_by_modulo():
    assert menstrual_phase(_ANCHOR, _on(1 + 28)).day == 1  # one full cycle later → day 1 again
    assert menstrual_phase(_ANCHOR, _on(1 + 28)).phase == "менструація"


def test_custom_length_moves_ovulation_and_pms():
    p = menstrual_phase(_ANCHOR, _on(30), length=32)  # ovulation ≈ 32−14 = 18
    assert p.length == 32 and p.phase == "ПМС"  # day 30 of 32 → last days


def test_format_cycle_shows_phase_day_and_note():
    text = format_cycle(menstrual_phase(_ANCHOR, _on(26)))
    assert "ПМС" in text and "день 26/28" in text and "—" in text


# --- the anchor parser ----------------------------------------------------
def test_parse_cycle_anchor_with_day1_label_and_length():
    assert parse_cycle_anchor(_NATAL) == (date(2026, 5, 25), 28)


def test_parse_cycle_anchor_defaults_length_and_handles_missing_or_bad():
    assert parse_cycle_anchor("Цикл: 01.01.2026.") == (date(2026, 1, 1), 28)  # default length
    assert parse_cycle_anchor("Сонце 15° Риб") is None  # no Цикл line
    assert parse_cycle_anchor("Цикл: 32.13.2026.") is None  # invalid date


# --- merge into the mood --------------------------------------------------
def _mood_core(tmp_path, llm, natal=_NATAL, cycle=True):
    return Core(
        llm=llm, repository=JsonRepository(tmp_path / "s.json"), canon="Ти — Лілі.",
        model="m", clock=_DAY, natal=natal, cycle_enabled=cycle,
    )


def test_mood_request_includes_the_cycle_and_an_integration_directive():
    _, msgs = mood_request("Сонце", "2026-06-08", cycle="овуляція (день 15/28) — пік")
    content = msgs[0]["content"]
    assert "гормональний цикл" in content and "овуляція (день 15/28)" in content
    assert "ІНТЕГРУЙ" in content  # the shared integration directive


def test_ensure_mood_feeds_the_cycle_into_the_mood_call(tmp_path):
    llm = MockLLMClient(_READING)
    core = _mood_core(tmp_path, llm)
    core._ensure_mood()
    content = llm.calls[0]["messages"][0]["content"]
    assert "гормональний цикл" in content and "овуляція" in content  # today (2026-06-08) → ovulation
    assert core.cycle is not None and core.cycle.phase == "овуляція"


def test_no_anchor_or_disabled_skips_the_cycle(tmp_path):
    no_anchor = _mood_core(tmp_path, MockLLMClient(_READING), natal="Народження: 05.03.2002, Львів.")
    no_anchor._ensure_mood()
    assert no_anchor.cycle is None
    assert "гормональний цикл" not in no_anchor._llm.calls[0]["messages"][0]["content"]
    assert no_anchor.mood is not None  # the mood still runs

    off = _mood_core(tmp_path, MockLLMClient(_READING), cycle=False)
    off._ensure_mood()
    assert off.cycle is None
    assert "гормональний цикл" not in off._llm.calls[0]["messages"][0]["content"]


# --- TUI ------------------------------------------------------------------
async def test_biorhythm_command_also_shows_the_cycle(tmp_path):
    from tui.app import ChatInput, LumiApp

    core = _mood_core(tmp_path, MockLLMClient(_READING))
    core.ensure_mood()  # compute + cache today's cycle
    app = LumiApp(core)
    async with app.run_test() as pilot:
        app.query_one("#prompt", ChatInput).text = "/biorhythm"
        await pilot.press("enter")
        await pilot.pause()
        assert any("Цикл:" in line for line in app.transcript)
        assert any("овуляція" in line for line in app.transcript)

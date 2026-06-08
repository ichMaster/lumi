"""Unit tests for the v0.8 biorhythm engine (LUMI-031) — exact, deterministic."""

import math
from datetime import date

import pytest

from core.biorhythm import (
    PERIODS,
    biorhythms,
    format_biorhythms,
    load_birth_date,
)
from core.config import load_config

BIRTH = date(2002, 3, 5)  # Лілі's natal birth date (core/natal.md)


def test_values_are_exact_sine_of_days_since_birth():
    today = date(2026, 6, 8)
    d = (today - BIRTH).days
    b = biorhythms(BIRTH, today)
    for cycle in b:
        period = PERIODS[cycle.name]
        assert cycle.value == pytest.approx(math.sin(2 * math.pi * d / period))


def test_deterministic_same_inputs_same_result():
    today = date(2026, 6, 8)
    assert biorhythms(BIRTH, today) == biorhythms(BIRTH, today)


def test_birth_day_is_all_zero_and_critical():
    b = biorhythms(BIRTH, BIRTH)  # d = 0 → sin(0) = 0 for every cycle
    for cycle in b:
        assert cycle.value == pytest.approx(0.0)
        assert cycle.label == "critical"


def test_high_label_at_a_physical_peak():
    # +6 days: sin(2π·6/23) ≈ 0.997 → high (not at a crossing).
    b = biorhythms(BIRTH, BIRTH.fromordinal(BIRTH.toordinal() + 6))
    assert b.physical.value > 0.7
    assert b.physical.label == "high"


def test_critical_label_at_a_physical_zero_crossing():
    # The physical cycle (23 d) crosses zero between day 11 and 12 → day 11 is critical.
    b = biorhythms(BIRTH, BIRTH.fromordinal(BIRTH.toordinal() + 11))
    assert b.physical.label == "critical"


def test_load_birth_date_from_the_authored_natal_file():
    assert load_birth_date(load_config(load_env=False).natal_path) == BIRTH


def test_load_birth_date_missing_or_garbled(tmp_path):
    assert load_birth_date(tmp_path / "nope.md") is None  # missing file
    bad = tmp_path / "natal.md"
    bad.write_text("Народження: невідомо, Львів.", encoding="utf-8")
    assert load_birth_date(bad) is None  # no parseable date
    bad.write_text("Народження: 32.13.2002, Львів.", encoding="utf-8")
    assert load_birth_date(bad) is None  # invalid date


def test_format_biorhythms_renders_all_three():
    text = format_biorhythms(biorhythms(BIRTH, date(2026, 6, 8)))
    for label in ("фізичний", "емоційний", "інтелектуальний"):
        assert label in text
    assert "(" in text and ")" in text  # each carries a phase label


# --- LUMI-032: merge into the v0.6 daily mood ----------------------------
from datetime import UTC, datetime  # noqa: E402

from core.agent import Core  # noqa: E402
from core.clock import fixed_clock  # noqa: E402
from core.llm import MockLLMClient  # noqa: E402
from core.mood import mood_request  # noqa: E402
from state.local_store import JsonRepository  # noqa: E402

_NATAL = "Народження: 05.03.2002, 10:15, Львів.\nСонце 15° Риб."
_READING = "День рухливий.\n\nРЕЗОЛЮЦІЯ:\nНастрій жвавий."
_DAY1 = fixed_clock(datetime(2026, 6, 8, 9, 0, tzinfo=UTC))


def _mood_core(tmp_path, llm, clock=_DAY1, natal=_NATAL, bio=True, mood=True):
    return Core(
        llm=llm, repository=JsonRepository(tmp_path / "s.json"), canon="Ти — Лілі.",
        model="m", clock=clock, natal=natal, mood_enabled=mood, biorhythms_enabled=bio,
    )


def test_parse_birth_date_from_text():
    from core.biorhythm import parse_birth_date

    assert parse_birth_date(_NATAL) == BIRTH
    assert parse_birth_date("Сонце 15° Риб") is None  # no birth line


def test_mood_request_includes_biorhythms_when_given():
    _, msgs = mood_request("Сонце", "2026-06-08", biorhythms="фізичний +1.00 (high)")
    assert "Біоритми" in msgs[0]["content"] and "фізичний +1.00 (high)" in msgs[0]["content"]


def test_mood_request_without_biorhythms_is_unchanged():
    _, msgs = mood_request("Сонце", "2026-06-08")
    assert "Біоритми" not in msgs[0]["content"]


def test_ensure_mood_feeds_biorhythms_into_the_mood_call(tmp_path):
    llm = MockLLMClient(_READING)
    core = _mood_core(tmp_path, llm)
    core._ensure_mood()
    content = llm.calls[0]["messages"][0]["content"]
    assert "Біоритми" in content and "фізичний" in content
    assert core.biorhythms is not None  # cached on the core


def test_biorhythms_cached_once_per_local_day_and_recomputes(tmp_path):
    llm = MockLLMClient(_READING)
    core = _mood_core(tmp_path, llm)
    core._ensure_mood()
    core._ensure_mood()  # same local day → cached, no second call
    assert len(llm.calls) == 1
    first = core.biorhythms
    core._clock = fixed_clock(datetime(2026, 6, 9, 1, 0, tzinfo=UTC))  # next local day
    core._ensure_mood()
    assert len(llm.calls) == 2 and core.biorhythms != first  # recomputed


def test_no_birth_date_runs_horoscope_only(tmp_path):
    llm = MockLLMClient(_READING)
    core = _mood_core(tmp_path, llm, natal="Сонце 15° Риб")  # no Народження line
    core._ensure_mood()
    assert "Біоритми" not in llm.calls[0]["messages"][0]["content"]
    assert core.biorhythms is None and core.mood is not None  # mood still runs


def test_biorhythms_disabled_skips_the_merge(tmp_path):
    llm = MockLLMClient(_READING)
    core = _mood_core(tmp_path, llm, bio=False)
    core._ensure_mood()
    assert "Біоритми" not in llm.calls[0]["messages"][0]["content"]
    assert core.biorhythms is None and core.mood is not None


def test_reply_still_answers_fully_with_biorhythms_merged(tmp_path):
    # The merge colors the mood; a turn still answers fully (never competence/blocking).
    llm = MockLLMClient(
        replies=_READING, states={"reply": "Звісно, поясню.", "emotion": "calm", "intensity": 0.5}
    )
    core = _mood_core(tmp_path, llm)
    out = core.reply("поясни рекурсію", core.start_session())
    assert out.reply == "Звісно, поясню."
    assert core.biorhythms is not None

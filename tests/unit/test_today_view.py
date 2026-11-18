"""v4.4 LUMI-314 — her day for the window's State (``core/mood.py`` ``mood_body``, ``core/biorhythm.py``
``biorhythm_detail``, ``Core.today_view``).

Pinned: the reading's body without its resolution and theme line; the biorhythms equal ``/biorhythm``'s, plus the trend
and the day in the cycle; today's mood only (never yesterday's), never computed by the view (no model call); the
closeness by name; ``plan`` waits for v5.7."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime

from core.agent import Core
from core.biorhythm import PERIODS, biorhythm_detail, biorhythms
from core.llm import MockLLMClient
from core.mood import mood_body, split_resolution
from state.local_store import JsonRepository

READING = (
    "Сонце в Рибах м'яко торкається Місяця.\n\n"
    "День тихий, без поспіху.\n\n"
    "**РЕЗОЛЮЦІЯ:** Тиха зосередженість; хочеться глибини, а не шуму.\n\n"
    "ТЕМА: quiet-collapse"
)
NATAL = "Сонце 15° Риб\nНародження: 14.03.2001, 03:10, Київ"
BIRTH = date(2001, 3, 14)


class _Clock:
    def __init__(self, when: datetime) -> None:
        self.now = when

    def __call__(self) -> datetime:
        return self.now


def _core(tmp_path, clock, *, natal=NATAL, bio=True, llm=None):
    return Core(llm=llm or MockLLMClient(READING), repository=JsonRepository(tmp_path / "s.json"), canon="C",
                model="m", clock=clock, natal=natal, mood_enabled=True, biorhythms_enabled=bio,
                theme_descriptions={"quiet-collapse": "вигорання"})


# --- mood_body ---------------------------------------------------------------------------------------------------
def test_the_body_drops_the_resolution_and_the_theme():
    assert mood_body(READING) == "Сонце в Рибах м'яко торкається Місяця.\n\nДень тихий, без поспіху."
    assert "Тиха зосередженість" in split_resolution(READING)


def test_the_body_with_the_marker_on_its_own_line():
    assert mood_body("Текст дня.\n\nРЕЗОЛЮЦІЯ:\nПідсумок.\nТЕМА: 3am") == "Текст дня."


def test_without_a_marker_the_last_paragraph_is_the_resolution_so_the_body_is_the_rest():
    reading = "Перший абзац.\n\nДругий абзац.\n\nОстанній — резюме дня."
    assert split_resolution(reading) == "Останній — резюме дня."
    assert mood_body(reading) == "Перший абзац.\n\nДругий абзац."
    assert mood_body("Лише один абзац.") == ""


# --- biorhythm_detail --------------------------------------------------------------------------------------------
def test_the_detail_equals_the_command_and_adds_the_trend_and_the_day():
    today = date(2026, 11, 18)
    detail = biorhythm_detail(BIRTH, today)
    plain = list(biorhythms(BIRTH, today))
    assert [c["name"] for c in detail] == ["physical", "emotional", "intellectual"]
    for got, cycle in zip(detail, plain, strict=True):
        assert (got["value"], got["label"]) == (cycle.value, cycle.label)
        assert got["period"] == PERIODS[cycle.name]
        d = (today - BIRTH).days
        assert got["day"] == d % got["period"]
        tomorrow = math.sin(2 * math.pi * (d + 1) / got["period"])
        assert got["trend"] == ("rising" if tomorrow > got["value"] else "falling")


def test_day_zero_rises_and_a_peak_falls():
    detail = {c["name"]: c for c in biorhythm_detail(BIRTH, BIRTH)}
    assert all(c["day"] == 0 and c["value"] == 0.0 and c["trend"] == "rising" for c in detail.values())
    after_peak = {c["name"]: c for c in biorhythm_detail(BIRTH, date(2001, 3, 20))}  # day 6 of 23: past the top
    assert after_peak["physical"]["day"] == 6 and after_peak["physical"]["trend"] == "falling"


# --- Core.today_view ---------------------------------------------------------------------------------------------
def test_today_is_the_structured_mood_biorhythm_and_closeness(tmp_path):
    clock = _Clock(datetime(2026, 11, 18, 9, 0, tzinfo=UTC))
    core = _core(tmp_path, clock)
    core.ensure_mood()
    calls = len(core._llm.calls)
    view = core.today_view()
    assert len(core._llm.calls) == calls  # the view itself never calls the model
    assert view["date"] == "2026-11-18" and view["plan"] is None
    assert view["mood"] == {"resolution": core.mood, "reading": mood_body(READING), "theme": "quiet-collapse",
                            "made_at": None}
    assert view["biorhythms"] == biorhythm_detail(BIRTH, date(2026, 11, 18))
    level, name = core.closeness_status()
    assert view["closeness"] == {"level": level, "name": name}


def test_the_view_never_computes_the_mood(tmp_path):
    core = _core(tmp_path, _Clock(datetime(2026, 11, 18, 9, 0, tzinfo=UTC)))
    view = core.today_view()
    assert view["mood"] is None and core._llm.calls == []
    assert len(view["biorhythms"]) == 3  # computed, so there before the mood


def test_yesterdays_mood_is_not_today_s(tmp_path):
    clock = _Clock(datetime(2026, 11, 17, 22, 0, tzinfo=UTC))
    core = _core(tmp_path, clock)
    core.ensure_mood()
    assert core.today_view()["mood"] is not None
    clock.now = datetime(2026, 11, 18, 0, 5, tzinfo=UTC)  # past midnight, no turn yet
    view = core.today_view()
    assert view["mood"] is None and view["date"] == "2026-11-18"


def test_no_biorhythms_when_off_or_without_a_birth_date(tmp_path):
    clock = _Clock(datetime(2026, 11, 18, 9, 0, tzinfo=UTC))
    assert _core(tmp_path / "off", clock, bio=False).today_view()["biorhythms"] == []
    assert _core(tmp_path / "nobirth", clock, natal="Сонце 15° Риб").today_view()["biorhythms"] == []


# --- v4.4 LUMI-319: the hormonal cycle ---------------------------------------------------------------------------
from datetime import timedelta  # noqa: E402

from core.cycle import PHASE_KEYS, cycle_detail, menstrual_phase  # noqa: E402

ANCHOR = date(2026, 5, 25)
NATAL_CYCLE = NATAL + "\nЦикл: день 1 — 25.05.2026, довжина 28 днів."


def test_cycle_detail_equals_the_command_day_by_day():
    for length in (21, 28, 35):
        for n in range(length * 2):
            today = ANCHOR + timedelta(days=n)
            detail = cycle_detail(ANCHOR, today, length)
            phase = menstrual_phase(ANCHOR, today, length)
            assert (detail["day"], detail["length"], detail["note"]) == (phase.day, length, phase.note)
            assert detail["phase"] == PHASE_KEYS[phase.phase]
            assert len(detail["phases"]) == length and detail["phases"][phase.day - 1] == detail["phase"]
            for i, key in enumerate(detail["phases"]):  # every day of the cycle, as the command names it
                assert key == PHASE_KEYS[menstrual_phase(ANCHOR, today + timedelta(days=i - phase.day + 1), length).phase]


def test_a_28_day_cycle_reads_as_the_core_draws_it():
    detail = cycle_detail(ANCHOR, ANCHOR, 28)
    assert detail["day"] == 1 and detail["phase"] == "menstrual"
    assert detail["phases"] == (["menstrual"] * 5 + ["follicular"] * 7 + ["ovulation"] * 3 + ["luteal"] * 8
                                + ["pms"] * 5)  # ovulation on days 13–15 (L − 14 ± 1), PMS the last five


def test_today_carries_the_cycle_when_on_and_none_without_the_anchor(tmp_path):
    clock = _Clock(datetime(2026, 11, 19, 9, 0, tzinfo=UTC))
    on = Core(llm=MockLLMClient(READING), repository=JsonRepository(tmp_path / "on.json"), canon="C", model="m",
              clock=clock, natal=NATAL_CYCLE, mood_enabled=True, cycle_enabled=True)
    assert on.today_view()["cycle"] == cycle_detail(ANCHOR, date(2026, 11, 19), 28)
    assert on.cycle_view()["phase"] in PHASE_KEYS.values()
    off = Core(llm=MockLLMClient(READING), repository=JsonRepository(tmp_path / "off.json"), canon="C", model="m",
               clock=clock, natal=NATAL_CYCLE, mood_enabled=True, cycle_enabled=False)
    assert off.today_view()["cycle"] is None
    assert _core(tmp_path / "noanchor", clock).today_view()["cycle"] is None  # NATAL has no «Цикл:» line

"""v2.1.1 — the face theme survives an echoed description, and the daily mood survives a restart.

Two live bugs (2026-10-04): the mood model often echoes the manifest line ("ТЕМА: 3am: Rooftop…"), which
matched no theme → the faces fell back to the default pack; and the mood was cached only in memory, so
every TUI start re-rolled it (4 mood calls in one day, the theme jumping 3am → quiet-collapse).
"""

from datetime import UTC, datetime

import pytest

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from core.mood import reading_from_log, split_theme
from state.local_store import JsonRepository

_DAY = fixed_clock(datetime(2026, 10, 4, 9, 0, tzinfo=UTC))
_THEMES = {"3am": "Rooftop loneliness at 3AM", "quiet-collapse": "Calm on the outside", "vigil": "Waiting"}


def _core(tmp_path, llm, *, log=None, sig=None):
    return Core(
        llm=llm, repository=JsonRepository(tmp_path / "s.json"),
        canon="C", model="m", clock=_DAY, natal="Сонце 15° Риб", mood_enabled=True,
        biorhythms_enabled=False, cycle_enabled=False,
        theme_descriptions=_THEMES, default_theme=None, face_signal=sig, mood_log_path=log,
    )


# --- split_theme: the leading slug, whatever the model wraps it in ----------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("ТЕМА: quiet-collapse", "quiet-collapse"),
        ("ТЕМА: 3am: Rooftop loneliness at 3AM — misty-eyed, headphones on", "3am"),  # the live bug
        ("ТЕМА: drowning: Overwhelm as stillness", "drowning"),
        ("ТЕМА: **Vigil**", "vigil"),
        ("ТЕМА: «im-fine» — посмішка", "im-fine"),
        ("Тема: calm-before.", "calm-before"),
        ("ТЕМА: немає", None),
    ],
)
def test_split_theme_keeps_only_the_slug(line, expected):
    assert split_theme(f"День.\n\nРЕЗОЛЮЦІЯ:\nтиша.\n\n{line}") == expected


def test_echoed_description_still_themes_the_face(tmp_path):
    sig = tmp_path / "face.txt"
    reading = "День.\n\nРЕЗОЛЮЦІЯ:\nтиша.\n\nТЕМА: 3am: Rooftop loneliness at 3AM — misty-eyed"
    core = _core(tmp_path, MockLLMClient(reading), sig=sig)
    core._ensure_mood()
    assert core.theme == "3am"
    core._write_face_signal("thoughtful", 0.7)
    assert sig.read_text(encoding="utf-8").startswith("3am thoughtful 0.70 ")  # not the bare default line


# --- reading_from_log: the last block for the day -----------------------------------------------------

_LOG = (
    "\n\n===== 2026-10-03 =====\nвчорашній настрій\nТЕМА: vigil\n"
    "\n\n===== 2026-10-04 =====\nранковий\n\nРЕЗОЛЮЦІЯ:\nперший.\n\nТЕМА: 3am\n"
    "\n\n===== 2026-10-04 =====\nпізніший\n\nРЕЗОЛЮЦІЯ:\nостанній.\n\nТЕМА: quiet-collapse\n"
)


def test_reading_from_log_takes_the_last_block_of_the_day():
    assert reading_from_log(_LOG, "2026-10-04").startswith("пізніший")
    assert reading_from_log(_LOG, "2026-10-03").startswith("вчорашній")


def test_reading_from_log_none_for_a_missing_day_or_empty_log():
    assert reading_from_log(_LOG, "2026-10-05") is None
    assert reading_from_log("", "2026-10-04") is None
    assert reading_from_log("\n\n===== 2026-10-04 =====\n   \n", "2026-10-04") is None


# --- the daily mood survives a restart ----------------------------------------------------------------


def test_restart_reuses_todays_logged_mood_without_a_call(tmp_path):
    log = tmp_path / "mood.log"
    log.write_text(_LOG, encoding="utf-8")
    llm = MockLLMClient("НОВИЙ\n\nРЕЗОЛЮЦІЯ:\nне має з'явитись.\n\nТЕМА: vigil")
    core = _core(tmp_path, llm, log=log)
    core._ensure_mood()
    assert llm.calls == []  # no new mood call — today's reading is reused
    assert core.mood == "останній."
    assert core.theme == "quiet-collapse"  # the same theme all day, no re-roll
    assert log.read_text(encoding="utf-8") == _LOG  # nothing appended


def test_a_new_day_still_computes_and_logs_once(tmp_path):
    log = tmp_path / "mood.log"
    log.write_text("\n\n===== 2026-10-03 =====\nвчора\nТЕМА: vigil\n", encoding="utf-8")
    llm = MockLLMClient("Сьогодні.\n\nРЕЗОЛЮЦІЯ:\nсвіжий.\n\nТЕМА: 3am: Rooftop…")
    core = _core(tmp_path, llm, log=log)
    core._ensure_mood()
    core._ensure_mood()  # a second turn the same day → still one call
    assert len(llm.calls) == 1
    assert core.mood == "свіжий." and core.theme == "3am"
    assert log.read_text(encoding="utf-8").count("===== 2026-10-04 =====") == 1


def test_two_starts_the_same_day_make_one_mood_call(tmp_path):
    log = tmp_path / "mood.log"
    first = MockLLMClient("Ранок.\n\nРЕЗОЛЮЦІЯ:\nодин.\n\nТЕМА: vigil")
    _core(tmp_path, first, log=log)._ensure_mood()
    second = MockLLMClient("Інший.\n\nРЕЗОЛЮЦІЯ:\nдва.\n\nТЕМА: 3am")
    restarted = _core(tmp_path, second, log=log)  # a fresh process, same day
    restarted._ensure_mood()
    assert len(first.calls) == 1 and second.calls == []
    assert restarted.mood == "один." and restarted.theme == "vigil"


def test_without_a_log_the_mood_is_computed_as_before(tmp_path):
    llm = MockLLMClient("День.\n\nРЕЗОЛЮЦІЯ:\nяк раніше.\n\nТЕМА: vigil")
    core = _core(tmp_path, llm, log=None)
    core._ensure_mood()
    assert len(llm.calls) == 1 and core.mood == "як раніше."

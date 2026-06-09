"""Mood picks the face theme + the core writes the themed signal (v0.11, LUMI-044)."""

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from core.mood import mood_request, split_theme, strip_theme
from state.local_store import JsonRepository

_DAY1 = fixed_clock(datetime(2026, 6, 7, 9, 0, tzinfo=UTC))


def _core(tmp_path, reading, *, themes, default, sig=None):
    return Core(
        llm=MockLLMClient(reading), repository=JsonRepository(tmp_path / "s.json"),
        canon="C", model="m", clock=_DAY1, natal="Сонце 15° Риб", mood_enabled=True,
        biorhythms_enabled=False, cycle_enabled=False,
        theme_descriptions=themes, default_theme=default, face_signal=sig,
    )


# --- mood_request + parsers -----------------------------------------------
def test_mood_request_lists_themes_and_asks_for_a_tag():
    _, msgs = mood_request("natal", "2026-06-07", themes={"cozy": "warm", "3am": "lonely"})
    content = msgs[0]["content"]
    assert "ТЕМА:" in content and "cozy: warm" in content and "3am: lonely" in content


def test_split_and_strip_theme():
    r = "текст\n\nРЕЗОЛЮЦІЯ: підсумок\n\nТЕМА: Cozy"
    assert split_theme(r) == "cozy"  # lowercased
    assert "ТЕМА" not in strip_theme(r) and "підсумок" in strip_theme(r)
    assert split_theme("no theme line here") is None


# --- mood → theme (validated, cached, kept off the resolution) ------------
def test_mood_picks_and_validates_a_theme(tmp_path):
    reading = "День теплий.\n\nРЕЗОЛЮЦІЯ:\nСпокій і тепло.\n\nТЕМА: cozy"
    core = _core(tmp_path, reading, themes={"cozy": "warm", "3am": "lonely"}, default="cozy")
    core._ensure_mood()
    assert core.theme == "cozy"
    assert core._mood.theme == "cozy"
    assert core._mood.resolution == "Спокій і тепло."  # the ТЕМА line is stripped out


def test_unknown_theme_falls_back_to_default(tmp_path):
    reading = "День.\n\nРЕЗОЛЮЦІЯ:\nтиша.\n\nТЕМА: nonexistent"
    core = _core(tmp_path, reading, themes={"cozy": "warm"}, default="cozy")
    core._ensure_mood()
    assert core._mood.theme is None and core.theme == "cozy"  # invalid pick → the default


def test_theme_defaults_when_mood_off_or_no_pick(tmp_path):
    # No themes offered → no pick, no default → bare (None).
    core = _core(tmp_path, "День.\n\nРЕЗОЛЮЦІЯ:\nтиша.", themes={}, default=None)
    core._ensure_mood()
    assert core.theme is None


# --- the themed face signal -----------------------------------------------
def test_themed_face_signal(tmp_path):
    sig = tmp_path / "face.txt"
    reading = "День.\n\nРЕЗОЛЮЦІЯ:\nтепло.\n\nТЕМА: cozy"
    core = _core(tmp_path, reading, themes={"cozy": "warm"}, default="cozy", sig=sig)
    core._ensure_mood()
    core._write_face_signal("joy", 0.8)
    assert sig.read_text(encoding="utf-8").startswith("cozy joy 0.80 ")  # <theme> <emotion> <int>


def test_bare_face_signal_without_a_theme(tmp_path):
    sig = tmp_path / "face.txt"
    core = _core(tmp_path, "День.\n\nРЕЗОЛЮЦІЯ:\nтиша.", themes={}, default=None, sig=sig)
    core._ensure_mood()
    core._write_face_signal("joy", 0.8)
    assert sig.read_text(encoding="utf-8").startswith("joy 0.80 ")  # the bare v0.7 line

"""Manual face-theme override — /theme <name> + /theme auto (v0.11.x, contract-free)."""

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from core.mood import MoodState
from state.local_store import JsonRepository

_DAY = fixed_clock(datetime(2026, 6, 9, 14, 0, tzinfo=UTC))
_THEMES = {"vigil": "candle", "3am": "rooftop", "calm-base": "neutral"}


def _core(tmp_path, **kw):
    return Core(llm=MockLLMClient("x"), repository=JsonRepository(tmp_path / "s.json"),
                canon="C", model="m", clock=_DAY, mood_enabled=False,
                theme_descriptions=_THEMES, default_theme="calm-base",
                face_signal=tmp_path / "face.txt", **kw)


def test_themes_lists_known(tmp_path):
    assert _core(tmp_path).themes == ["3am", "calm-base", "vigil"]  # sorted manifest themes


def test_set_valid_theme_overrides(tmp_path):
    core = _core(tmp_path)
    assert core.theme == "calm-base"  # default
    assert core.set_theme("vigil") is True
    assert core.theme == "vigil"  # override active


def test_override_beats_the_mood_pick(tmp_path):
    core = _core(tmp_path)
    core._mood = MoodState(date="2026-06-09", resolution="…", reading="…", theme="3am")
    assert core.theme == "3am"  # the mood's pick
    core.set_theme("vigil")
    assert core.theme == "vigil"  # the override wins over the mood


def test_auto_clears_override(tmp_path):
    core = _core(tmp_path)
    core.set_theme("vigil")
    assert core.set_theme("auto") is True
    assert core.theme == "calm-base"  # back to the mood/default
    core.set_theme("vigil")
    assert core.set_theme(None) is True  # None also clears
    assert core.theme == "calm-base"


def test_unknown_theme_rejected(tmp_path):
    core = _core(tmp_path)
    core.set_theme("vigil")
    assert core.set_theme("nope") is False  # unknown
    assert core.theme == "vigil"  # override unchanged


def test_signal_carries_the_forced_theme(tmp_path):
    sig = tmp_path / "face.txt"
    core = _core(tmp_path)
    core._write_face_signal("joy", 0.8)
    assert sig.read_text(encoding="utf-8").startswith("calm-base joy 0.80 ")
    core.set_theme("vigil")  # re-emits the signal with the last emotion
    assert sig.read_text(encoding="utf-8").startswith("vigil joy 0.80 ")

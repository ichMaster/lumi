"""v4.8 LUMI-337 — the clients' own lines are English: the terminal client, the viewer's title.

Her words and the texts a client hands her as a turn are not checked here (LUMI-335 pins those)."""

from pathlib import Path

from tui import app as tui_app

ROOT = Path(__file__).resolve().parents[2]
CYRILLIC = set("абвгґдеєжзиіїйклмнопрстуфхцчшщьюяАБВГҐДЕЄЖЗИІЇЙКЛМНОПРСТУФХЦЧШЩЬЮЯ")


def _english(text: str) -> bool:
    return not any(ch in CYRILLIC for ch in text)


def test_her_name_and_the_speaker_labels_are_english():
    assert tui_app.LILI_LABEL == "Lili"
    assert tui_app.VOICE_LABEL == "🎤 you"
    assert tui_app.LumiApp.TITLE == "Lumi — Lili"
    assert _english(tui_app.ERROR_LINE) and _english(tui_app.USER_LABEL) and _english(tui_app.TELEGRAM_LABEL)


def test_every_thought_kind_reads_in_english():
    for name, verb in tui_app._THOUGHT_VERBS.items():
        assert _english(verb), name
        assert tui_app.thought_meta_line(name) == f"✦ Lili {verb}…"
    assert tui_app.thought_meta_line("unknown") == "✦ Lili is musing…"


def test_the_viewer_window_is_titled_lili():
    assert 'set_caption("Lili")' in (ROOT / "viewer" / "app.py").read_text(encoding="utf-8")

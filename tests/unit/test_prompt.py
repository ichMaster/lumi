"""Unit tests for canon loading + system-prompt assembly (LUMI-003)."""

import pytest

from core.config import load_config
from core.prompt import build_system_prompt, load_canon

# The 9-emotion enum the canon's palette must cover (EMOTION.md §4).
EMOTIONS = ("joy", "calm", "playful", "tender", "thoughtful", "serious", "surprise", "doubt", "sad")


def test_load_canon_reads_the_configured_file():
    cfg = load_config(load_env=False)
    canon = load_canon(cfg.canon_path)
    assert "Лілі" in canon
    assert len(canon) > 200


def test_build_system_prompt_places_canon_into_system_field():
    canon = "Ти — Лілі. Ось твій характер."
    system = build_system_prompt(canon)
    assert canon in system  # the canon rides in the system prompt (the extension seam)


def test_build_system_prompt_is_verbatim_in_v0_1():
    canon = "exact character content"
    assert build_system_prompt(canon) == canon


def test_load_canon_missing_file_raises_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="Canon file not found"):
        load_canon(tmp_path / "does-not-exist.md")


def test_load_canon_empty_file_raises(tmp_path):
    empty = tmp_path / "empty.md"
    empty.write_text("   \n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        load_canon(empty)


def test_canon_covers_all_nine_emotions():
    cfg = load_config(load_env=False)
    canon = load_canon(cfg.canon_path)
    for emotion in EMOTIONS:
        assert emotion in canon, f"canon palette is missing emotion '{emotion}'"

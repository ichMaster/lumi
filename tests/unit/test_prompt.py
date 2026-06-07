"""Unit tests for canon loading + system-prompt assembly (LUMI-003)."""

import pytest

from core.config import load_config
from core.prompt import build_system_prompt, load_canon, split_reasoning

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


def test_build_system_prompt_is_verbatim_without_memory():
    canon = "exact character content"
    assert build_system_prompt(canon) == canon
    assert build_system_prompt(canon, summaries=[], facts=[]) == canon


def test_build_system_prompt_composes_memory_around_canon():
    canon = "Ти — Лілі."
    system = build_system_prompt(canon, summaries=["Минулого разу говорили про гори."],
                                 facts=["Зі Львова", "Любить каву"])
    # Canon at the base; summaries and facts composed around it.
    assert system.startswith(canon)
    assert "Минулого разу говорили про гори." in system
    assert "Зі Львова" in system
    assert "Любить каву" in system
    # Assembly order: canon → summaries → facts (ARCHITECTURE §Data model).
    assert system.index(canon) < system.index("Минулого") < system.index("Зі Львова")


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


def test_split_reasoning_extracts_think_tags():
    thinking, reply = split_reasoning("<think>думаю. гра слів.</think>Зате я анекдот вдома.")
    assert thinking == "думаю. гра слів."
    assert reply == "Зате я анекдот вдома."


def test_split_reasoning_no_tags_is_clean_reply():
    thinking, reply = split_reasoning("Просто відповідь, без міркувань.")
    assert thinking is None
    assert reply == "Просто відповідь, без міркувань."


def test_split_reasoning_handles_multiline_and_stray_tags():
    raw = "<think>\nрядок1\nрядок2\n</think>\nвідповідь</think>"
    thinking, reply = split_reasoning(raw)
    assert thinking == "рядок1\nрядок2"
    assert reply == "відповідь"  # stray closing tag stripped too
    assert "<think" not in reply and "</think" not in reply


def test_build_system_prompt_emotion_instruction_is_opt_in():
    from core.prompt import EMOTION_INSTRUCTION
    # Off by default → canon verbatim (the v0.1 contract).
    assert build_system_prompt("CANON") == "CANON"
    assert EMOTION_INSTRUCTION not in build_system_prompt("CANON")
    # emotion=True injects it right after the canon.
    withe = build_system_prompt("CANON", emotion=True)
    assert EMOTION_INSTRUCTION in withe
    assert withe.index("CANON") < withe.index(EMOTION_INSTRUCTION)

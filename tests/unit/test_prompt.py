"""Unit tests for canon loading + system-prompt assembly (LUMI-003)."""

import pytest

from core.config import load_config
from core.prompt import build_system_prompt, load_canon, split_emotion, split_reasoning

# The 9-emotion enum the canon's palette must cover (EMOTION.md §4).
EMOTIONS = ("joy", "calm", "playful", "tender", "thoughtful", "serious", "surprise", "doubt", "sad")


def test_load_canon_reads_the_configured_file():
    cfg = load_config(load_env=False)
    canon = load_canon(cfg.canon_path)
    # name-agnostic: the persona name may be re-authored (e.g. Лілі → Стхіра); assert the canon loads
    # with its expected structure, not a specific name.
    assert canon.lstrip().startswith("Ти —") and "## Хто ти" in canon
    assert len(canon) > 200


def test_build_system_prompt_places_canon_into_system_field():
    canon = "Ти — Лілі. Ось твій характер."
    system, _ = build_system_prompt(canon)
    assert canon in system  # the canon rides in the system prompt (the extension seam)


def test_build_system_prompt_is_verbatim_without_memory():
    canon = "exact character content"
    assert build_system_prompt(canon) == (canon, canon)  # (system, cache_prefix)
    assert build_system_prompt(canon, summaries=[], facts=[]) == (canon, canon)


def test_build_system_prompt_composes_memory_around_canon():
    canon = "Ти — Лілі."
    system, _ = build_system_prompt(
        canon, summaries=["Минулого разу говорили про гори."], facts=["Зі Львова", "Любить каву"])
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
    assert build_system_prompt("CANON") == ("CANON", "CANON")
    assert EMOTION_INSTRUCTION not in build_system_prompt("CANON")[0]
    # emotion=True injects it right after the canon.
    withe, _ = build_system_prompt("CANON", emotion=True)
    assert EMOTION_INSTRUCTION in withe
    assert withe.index("CANON") < withe.index(EMOTION_INSTRUCTION)


def test_build_system_prompt_cache_prefix_excludes_per_turn_blocks():
    # v0.15: stable blocks (instructions / memory / mood) form the cache_prefix; the per-turn
    # blocks (ambient / closeness / thoughts) + style ride in the tail, never the cached prefix.
    from core.prompt import EMOTION_INSTRUCTION
    system, cache_prefix = build_system_prompt(
        "CANON", emotion=True, facts=["F-STABLE"], mood="MOOD-STABLE",
        ambient="AMB-TURN", closeness="CLOSE-TURN", thoughts="THINK-TURN", style="STYLE-TAIL",
    )
    assert system.startswith(cache_prefix)  # cache_prefix is a true prefix of the full system
    # stable blocks live in the cached prefix
    assert EMOTION_INSTRUCTION in cache_prefix
    assert "F-STABLE" in cache_prefix and "MOOD-STABLE" in cache_prefix
    # per-turn blocks (+ style) are in the TAIL, never the cached prefix
    for per_turn in ("AMB-TURN", "CLOSE-TURN", "THINK-TURN", "STYLE-TAIL"):
        assert per_turn not in cache_prefix, f"{per_turn} leaked into the cache prefix"
        assert per_turn in system
    assert system.rstrip().endswith("STYLE-TAIL")  # style is still last (most salient)


def test_split_emotion_parses_and_strips():
    emo, clean = split_emotion("Привіт! <emotion>joy 0.8</emotion>")
    assert emo == {"emotion": "joy", "intensity": 0.8}
    assert clean == "Привіт!"


def test_split_emotion_name_only():
    emo, clean = split_emotion("ок <emotion>calm</emotion>")
    assert emo == {"emotion": "calm"}
    assert clean == "ок"


def test_split_emotion_no_tag_is_clean_text():
    assert split_emotion("просто текст") == (None, "просто текст")


def test_split_emotion_strips_stray_tag():
    _, clean = split_emotion("текст <emotion>joy 0.5</emotion> хвіст")
    assert "<emotion" not in clean and "</emotion" not in clean


def test_mark_cache_breakpoint_is_display_only_and_recoverable():
    from core.prompt import CACHE_BREAKPOINT_MARKER, mark_cache_breakpoint
    system, cache_prefix = build_system_prompt("CANON", emotion=True, mood="MOOD", ambient="AMB")
    marked = mark_cache_breakpoint(system, cache_prefix)
    # the divider sits exactly at the prefix/tail boundary (after mood, before ambient)
    assert CACHE_BREAKPOINT_MARKER in marked
    assert marked.index("MOOD") < marked.index(CACHE_BREAKPOINT_MARKER) < marked.index("AMB")
    # removing the divider yields the original system byte-for-byte (it never touches the real prompt)
    assert marked.replace("\n\n" + CACHE_BREAKPOINT_MARKER, "") == system
    # None / whole-system / not-a-prefix → unchanged
    assert mark_cache_breakpoint(system, None) == system
    assert mark_cache_breakpoint(system, system) == system
    assert mark_cache_breakpoint(system, "NOTAPREFIX") == system

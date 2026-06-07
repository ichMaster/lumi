"""Unit tests for the v0.5 emoji map + renderer (LUMI-023)."""

from core.emoji import BUILTIN, EmojiRenderer, emoji_for, load_emoji_map
from core.emotion import Emotion, EmotionState, IEmotionRenderer


def _state(emotion: Emotion, intensity: float) -> EmotionState:
    return EmotionState(reply="x", emotion=emotion, intensity=intensity)


def test_builtin_is_total_over_the_enum():
    assert set(BUILTIN) == set(Emotion)  # every emotion has a row
    assert all(len(row) == 3 for row in BUILTIN.values())


def test_intensity_selects_the_band():
    assert emoji_for(_state(Emotion.JOY, 0.2)) == "😄"      # low
    assert emoji_for(_state(Emotion.JOY, 0.5)) == "😄✨"     # mid (default)
    assert emoji_for(_state(Emotion.JOY, 0.9)) == "😄✨✨"   # high
    assert emoji_for(_state(Emotion.SAD, 0.9)) == "😢😢😢"  # repeat-style high


def test_calm_does_not_escalate():
    assert {emoji_for(_state(Emotion.CALM, i)) for i in (0.1, 0.5, 0.95)} == {"🙂"}


def test_resolved_map_is_total_over_the_enum_for_every_band():
    for emotion in Emotion:
        for intensity in (0.1, 0.5, 0.9):
            assert emoji_for(_state(emotion, intensity))  # non-empty glyph, no KeyError


def test_loader_uses_the_authored_default_file():
    from core.config import DEFAULT_EMOJI_PATH

    table = load_emoji_map(DEFAULT_EMOJI_PATH)
    assert set(table) == set(Emotion)
    assert table[Emotion.JOY] == ("😄", "😄✨", "😄✨✨")
    assert table[Emotion.CALM] == ("🙂", "🙂", "🙂")  # single glyph → all bands


def test_loader_missing_file_falls_back_to_builtin(tmp_path):
    assert load_emoji_map(tmp_path / "nope.md") == BUILTIN


def test_editing_a_row_takes_effect_and_stays_total(tmp_path):
    f = tmp_path / "emoji.md"
    f.write_text(
        "# my map\njoy = 🤩 | 🤩 | 🤩🎉\nunknownmood = 👽\n", encoding="utf-8"
    )
    table = load_emoji_map(f)
    assert table[Emotion.JOY] == ("🤩", "🤩", "🤩🎉")  # changed row applied
    assert table[Emotion.SAD] == BUILTIN[Emotion.SAD]   # untouched row keeps default
    assert set(table) == set(Emotion)                    # unknown name skipped; still total


def test_emoji_renderer_implements_the_interface_and_resolves():
    r = EmojiRenderer()
    assert isinstance(r, IEmotionRenderer)  # runtime-checkable Protocol
    r.render(_state(Emotion.TENDER, 0.8))
    assert r.last_glyph == "🥰💕💕"
    r.set_speaking(True)  # no-ops, never raise
    r.tick(16)

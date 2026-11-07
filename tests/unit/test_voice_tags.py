"""v2.7.1 — her voice tags, the pure pieces (`core/voice_tags.py`): the split, the safety rule, the stream filter."""

import pytest

from core.voice_tags import TAG_MODELS, VoiceTagStreamFilter, renders_tags, speakable, split_tags

LADDER = "[starts laughing] Ага, звісно. [laughs] Я так і думала. [laughs harder] Ну ти даєш!"


def test_the_text_loses_every_tag_and_the_speech_keeps_them():
    text, speech = split_tags(LADDER)
    assert text == "Ага, звісно. Я так і думала. Ну ти даєш!"
    assert speech == LADDER


@pytest.mark.parametrize("reply", [
    "Дивись [1] і arr[i] = 3 і [ОСТРІВ 3]",   # a footnote, an index, a bracket with a digit
    "Ось [тут](https://example.org) все є.",   # a link's text
    "Без жодної позначки.",
])
def test_brackets_that_are_not_tags_stay(reply):
    assert split_tags(reply) == (reply, reply)


@pytest.mark.parametrize(("reply", "text"), [
    ("Так [laughs]!", "Так!"),                               # nothing before punctuation
    ("[thoughtful] Ну…\n[whispers] добраніч", "Ну…\nдобраніч"),  # nothing at a line's edge
    ("«[sighs] Отак»", "«Отак»"),                             # nothing after an opening quote
    ("Мені [зітхає] трохи сумно", "Мені трохи сумно"),        # a Ukrainian tag goes too
    ("Ну [hesitant] [whispers] гаразд", "Ну гаразд"),         # a run of tags leaves one space
])
def test_the_spaces_are_tidied(reply, text):
    assert split_tags(reply)[0] == text


def test_only_models_that_perform_tags_receive_them():
    assert renders_tags("eleven_v4_turbo") and renders_tags(" eleven_v3 ")
    assert not renders_tags("eleven_turbo_v2_5") and not renders_tags("eleven_multilingual_v2")
    assert not renders_tags(None)
    assert speakable("eleven_v4_turbo", LADDER) == LADDER
    assert speakable("eleven_turbo_v2_5", LADDER) == "Ага, звісно. Я так і думала. Ну ти даєш!"  # never read aloud
    assert "eleven_v4_turbo" in TAG_MODELS


def _stream(chunks: list[str]) -> str:
    f = VoiceTagStreamFilter()
    return "".join(f.feed(c) for c in chunks) + f.flush()


def test_a_tag_split_across_chunks_never_shows():
    shown = _stream(["Ага, [sta", "rts laugh", "ing] звісно. [la", "ughs] Ну"])
    assert "[" not in shown and "]" not in shown and "звісно" in shown and shown.endswith("Ну")


def test_the_stream_keeps_brackets_that_are_not_tags():
    assert _stream(["Дивись [", "1] і [ОСТ", "РІВ 3] ось"]) == "Дивись [1] і [ОСТРІВ 3] ось"
    assert _stream(["Ось [тут", "](https://e.org) є"]) == "Ось [тут](https://e.org) є"


def test_the_stream_end_drops_a_closing_tag_and_keeps_an_unclosed_bracket():
    assert _stream(["Добраніч [whispers]"]) == "Добраніч "
    assert _stream(["Зараз [ну"]) == "Зараз [ну"


def test_a_long_bracket_is_released_once_it_cannot_be_a_tag():
    long = "[" + "а" * 45 + "]"
    assert _stream([long]) == long

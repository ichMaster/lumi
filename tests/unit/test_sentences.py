"""v1.4 LUMI-190 — the pure sentence chunker used by the voicer."""

from voice.sentences import split_sentences


def test_splits_on_sentence_punctuation_keeping_it():
    assert split_sentences("Привіт. Як ти? Добре!") == ["Привіт.", "Як ти?", "Добре!"]


def test_no_terminal_punctuation_is_one_chunk_flushed_tail():
    assert split_sentences("одне речення без крапки") == ["одне речення без крапки"]


def test_trailing_partial_after_a_sentence_is_flushed():
    assert split_sentences("Готово. І ще трохи") == ["Готово.", "І ще трохи"]


def test_never_splits_mid_word_only_on_whitespace():
    # The abbreviation's dot has no following whitespace inside "3.14", so it stays whole.
    assert split_sentences("Число 3.14 точне") == ["Число 3.14 точне"]


def test_multi_punctuation_stays_together():
    assert split_sentences("Що?! Справді…") == ["Що?!", "Справді…"]


def test_newlines_count_as_whitespace():
    assert split_sentences("Рядок один.\nРядок два.") == ["Рядок один.", "Рядок два."]


def test_blank_input_is_empty_list():
    assert split_sentences("") == []
    assert split_sentences("   \n  ") == []


def test_ellipsis_terminator():
    assert split_sentences("Ну… Гаразд.") == ["Ну…", "Гаразд."]

"""v0.33 LUMI-128 — the de-identification of thought-driven external queries (pure)."""
from __future__ import annotations

from core.deidentify import REDACTION, deidentify, personal_terms, topic_words


def test_personal_terms_picks_capitalised_proper_nouns():
    terms = personal_terms(["користувача звати Олег", "він із Львова", "любить каву"])
    assert "Олег" in terms and "Львова" in terms
    assert "звати" not in terms and "каву" not in terms  # lowercase common words excluded
    assert "із" not in terms                             # < 3 letters excluded


def test_deidentify_redacts_case_insensitively():
    out = deidentify("розкажи про олега і про погоду", ["Олег"])
    assert "олег" not in out.lower() and REDACTION in out and "погоду" in out  # topical part kept


def test_deidentify_catches_declensions_as_a_stem():
    # Ukrainian declension: a name stem matches its declined forms (privacy-first over-redaction).
    out = deidentify("про Олега, Олегович і Олегу", ["Олег"])
    assert "Олег" not in out and out.count(REDACTION) == 3


def test_deidentify_no_terms_is_unchanged():
    assert deidentify("just a topic", []) == "just a topic"
    assert deidentify("nothing personal", ["ab"]) == "nothing personal"  # < 3 → ignored


def test_topic_words_extracts_typed_words():
    assert topic_words("події у Львові наступний тиждень") == ["події", "Львові", "наступний", "тиждень"]
    assert topic_words("") == []


def test_keep_whitelists_the_users_own_topic_word():
    terms = personal_terms(["він із Львова", "живе у Львові", "бульвар Шевченка"])  # Львів-stems + Бульвар
    q = "події у Львові наступний тиждень"
    assert deidentify(q, terms) == "події у […] наступний тиждень"          # default: city redacted
    # the user explicitly typed "Львові" → keep it (stem "Львів" dropped from the redaction set)
    assert deidentify(q, terms, keep=topic_words(q)) == q                   # nothing redacted — survives


def test_keep_still_redacts_other_personal_terms():
    terms = personal_terms(["він із Львова", "його звати Олег"])
    out = deidentify("події у Львові з Олегом", terms, keep=["Львові"])     # keep the city, not the name
    assert "Львові" in out and "Олег" not in out and REDACTION in out

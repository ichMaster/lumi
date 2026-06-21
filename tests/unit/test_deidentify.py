"""v0.33 LUMI-128 — the de-identification of thought-driven external queries (pure)."""
from __future__ import annotations

from core.deidentify import REDACTION, deidentify, personal_terms


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

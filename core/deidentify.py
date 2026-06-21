"""De-identification of thought-driven external queries (v0.33 LUMI-128).

A thought-driven external query (wiki/news/web search, the ``%imagine`` gen prompt) is seeded by her
**inner state**, so it must be **de-identified** before it leaves: only the topical/creative part may reach
an external service — stricter than the v0.21/v0.25 reply-path "no personal data in queries" rule. This
module is **pure**: it extracts the user's personal terms (proper-noun-like tokens from their own memory)
and **redacts** them from an outgoing query. ``%prompt`` is **exempt** (the owner authored the instruction).

Over-redaction is privacy-safe — when in doubt a proper noun is stripped, leaving the topical part.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

REDACTION = "[…]"

# A "personal term" candidate: a word of ≥ 3 letters (any script, no digits/underscore). The proper-noun
# heuristic (capitalised first letter) drawn from the user's OWN memory catches names/places to redact.
_WORD_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)


def personal_terms(texts: Iterable[str]) -> set[str]:
    """Proper-noun-like tokens (capitalised, ≥ 3 letters) from the user's memory texts."""
    terms: set[str] = set()
    for text in texts:
        for word in _WORD_RE.findall(text or ""):
            if word[:1].isupper():
                terms.add(word)
    return terms


def topic_words(text: str) -> list[str]:
    """The ≥ 3-letter words of a user-typed topic — the basis for the de-id ``keep`` whitelist (they
    explicitly chose to search these, so a place/name *they* typed must not be redacted out)."""
    return _WORD_RE.findall(text or "")


def deidentify(text: str, terms: Iterable[str], *, keep: Iterable[str] = ()) -> str:
    """Redact any of ``terms`` (case-insensitive, as a **word stem**) from ``text`` → :data:`REDACTION`.

    A term matches at a word boundary plus any trailing letters, so Ukrainian declensions are caught
    (``Олег`` → ``Олега`` / ``Олегович``) — over-redaction is privacy-safe. Longer terms first; no terms →
    ``text`` unchanged.

    ``keep`` is a whitelist of words the user explicitly typed (their topic): any term that would redact
    one of them — i.e. that a keep-word *starts with* — is dropped from the redaction set, so a place or
    name the user themselves asked about survives (the leakage rule targets her *inner* seeds, not the
    user's own request). The autonomous (no-topic) path passes no ``keep`` → full redaction, unchanged.
    """
    uniq = {t for t in terms if t and len(t) >= 3}
    keep_lower = [w.lower() for w in keep if w]
    if keep_lower:  # drop terms the user's own topic words would trip (e.g. "Львові" protects stem "Львів")
        uniq = {t for t in uniq if not any(w.startswith(t.lower()) for w in keep_lower)}
    uniq = sorted(uniq, key=len, reverse=True)
    if not uniq:
        return text
    stem = "|".join(re.escape(t) for t in uniq)
    pattern = re.compile(rf"\b(?:{stem})[^\W\d_]*", re.IGNORECASE | re.UNICODE)
    return pattern.sub(REDACTION, text)

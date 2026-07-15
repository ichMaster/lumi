"""v1.4 (LUMI-190) — a pure sentence chunker for the voicer.

Splits a complete reply into **ordered, whole** sentences so the voicer can synth+play them one at a
time (lower time-to-first-audio) instead of waiting on the whole text. No I/O — unit-tested in isolation.
Splitting happens only on whitespace that *follows* sentence punctuation (``. ! ? …``), so a word is
never cut; text with no terminal punctuation comes back as a single chunk (the tail is flushed).
"""

from __future__ import annotations

import re

# Split at the whitespace that FOLLOWS one-or-more sentence-terminators — so "Що?!" stays whole and a
# word is never split (we only ever break on whitespace). Newlines count as whitespace.
_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")


def split_sentences(text: str) -> list[str]:
    """Return ``text`` as ordered whole sentences (empty list for blank input).

    A sentence keeps its terminating punctuation; a trailing run without terminal punctuation is
    returned as the final chunk (flushed, not dropped). Never splits mid-word.
    """
    parts = (p.strip() for p in _SPLIT_RE.split(text.strip()))
    return [p for p in parts if p]

"""Message chunking for semantic recall (v0.30).

A long message — a pasted chapter, a wall of reflection — is embedded by v0.16 as **one averaged
vector**, so a query about one part of it matches weakly. Chunking changes the **unit indexed**: a
message longer than ``threshold`` is split into ~``chunk_chars`` passages (on paragraph/sentence
boundaries, with a small ``overlap`` so a boundary sentence is reachable from either side), each
embedded as its own vector. A short message stays **one chunk** — exactly v0.16 behaviour.

This module is pure and model-free: :func:`chunk_text` is a deterministic, order-preserving split with
full coverage (concatenating the chunks, minus the overlap, reproduces the text). See
SEMANTIC_RECALL_CHUNKING.md. The vector indexing (LUMI-117) and the two-level expansion (LUMI-118)
build on it.
"""
from __future__ import annotations

import re

# A sentence end: . ! ? … (optionally closing quotes/brackets) followed by whitespace.
_SENT_RE = re.compile(r"[.!?…][\"»”'’)\]]*\s")


def chunk_text(
    text: str, *, chunk_chars: int = 800, overlap: int = 120, threshold: int = 1200
) -> list[str]:
    """Split ``text`` into ordered ~``chunk_chars`` passages on paragraph/sentence boundaries.

    - ``text`` at or below ``threshold`` chars → ``[text]`` (one chunk; the v0.16 ``chunk_count == 1``
      case). Empty / whitespace-only → ``[]`` (the caller already skips empty messages).
    - Above the threshold → several passages: each ~``chunk_chars``, broken at the latest
      paragraph/sentence/word boundary in the second half of the window (so chunks are neither tiny nor
      mid-word), with adjacent chunks sharing ``overlap`` chars. A run with no boundary (one huge token)
      is hard-cut at ``chunk_chars``.

    Deterministic, order-preserving, full coverage; never raises.
    """
    if not text or not text.strip():
        return []
    if len(text) <= threshold:
        return [text]
    chunk_chars = max(1, chunk_chars)
    overlap = max(0, min(overlap, chunk_chars - 1))
    chunks: list[str] = []
    n = len(text)
    start = 0
    while start < n:
        end = min(start + chunk_chars, n)
        if end < n:
            end = _best_break(text, start, end)
        chunks.append(text[start:end])
        if end >= n:
            break
        nxt = end - overlap
        start = nxt if nxt > start else end  # always make progress (no infinite loop)
    return chunks


def _best_break(text: str, start: int, target: int) -> int:
    """The break index in ``(start, target]`` at the latest paragraph/sentence/word boundary in the
    window's **second half** (avoids tiny chunks and mid-word cuts); falls back to ``target`` (a hard
    cut) when the window has no boundary — i.e. a single huge unbroken token."""
    window = text[start:target]
    lo = max(1, len(window) // 2)  # only accept a boundary in the second half of the window
    region = window[lo:]
    para = region.rfind("\n\n")  # paragraph boundary (preferred)
    if para != -1:
        return start + lo + para + 2
    sents = list(_SENT_RE.finditer(region))  # sentence boundary
    if sents:
        return start + lo + sents[-1].end()
    word = max(region.rfind(" "), region.rfind("\n"))  # word boundary (last resort)
    if word != -1:
        return start + lo + word + 1
    return target  # no boundary in the second half → hard cut

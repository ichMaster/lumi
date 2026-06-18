"""v0.30 LUMI-116 — the message chunker (core/chunking.py). Pure, no embedder, no store."""
from __future__ import annotations

from core.chunking import chunk_text
from core.config import Config


# --- short / empty: one chunk or none (v0.16 behaviour) -------------------------------------------
def test_short_message_is_one_chunk():
    assert chunk_text("коротко", threshold=1200) == ["коротко"]
    txt = "x" * 1200
    assert chunk_text(txt, threshold=1200) == [txt]  # at the threshold → still one chunk


def test_empty_or_whitespace_is_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


# --- long: several passages, sized, with overlap, full coverage -----------------------------------
_LONG = "\n\n".join(" ".join(f"Речення номер {i} тут." for i in range(1, 13)) for _ in range(6))


def test_long_message_splits_into_several_sized_chunks():
    chunks = chunk_text(_LONG, chunk_chars=300, overlap=60, threshold=200)
    assert len(chunks) >= 3
    assert all(len(c) <= 300 for c in chunks)  # none exceeds the target window


def test_full_coverage_via_overlap_reconstruction():
    overlap = 60
    chunks = chunk_text(_LONG, chunk_chars=300, overlap=overlap, threshold=200)
    recon = chunks[0] + "".join(c[overlap:] for c in chunks[1:])  # de-overlap the joins
    assert recon == _LONG  # nothing dropped or duplicated


def test_breaks_on_boundaries_not_midword():
    txt = "Перше речення тут. " * 40
    chunks = chunk_text(txt, chunk_chars=200, overlap=40, threshold=100)
    assert len(chunks) > 1
    for c in chunks[:-1]:  # every non-final chunk ends at a sentence/word boundary (a space here)
        assert c.endswith(" ")


def test_prefers_paragraph_boundary():
    # two paragraphs; a chunk break in the window should land at the blank line.
    txt = ("А" * 250) + "\n\n" + ("Б" * 250)
    chunks = chunk_text(txt, chunk_chars=300, overlap=20, threshold=100)
    assert chunks[0].endswith("\n\n")  # broke on the paragraph boundary


# --- degenerate: one huge unbroken token — hard cut, no raise, coverage holds ----------------------
def test_single_huge_token_hard_cuts_without_raising():
    txt = "x" * 1000  # no whitespace at all
    chunks = chunk_text(txt, chunk_chars=200, overlap=40, threshold=100)
    assert len(chunks) > 1 and all(len(c) <= 200 for c in chunks)
    assert chunks[0] + "".join(c[40:] for c in chunks[1:]) == txt  # still full coverage


def test_deterministic():
    a = chunk_text(_LONG, chunk_chars=250, overlap=50, threshold=100)
    b = chunk_text(_LONG, chunk_chars=250, overlap=50, threshold=100)
    assert a == b


# --- config defaults (off by default) -------------------------------------------------------------
def test_chunk_config_defaults():
    cfg = Config()
    assert cfg.rag_chunk is False
    assert cfg.rag_chunk_chars == 800 and cfg.rag_chunk_overlap == 120
    assert cfg.rag_chunk_threshold == 1200 and cfg.rag_chunk_w == 1

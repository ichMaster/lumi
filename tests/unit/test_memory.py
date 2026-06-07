"""Unit tests for memory helpers (LUMI-008 windowing; summary scaling)."""

from core.memory import SUMMARY_SYSTEM, summary_request, summary_sentences, trim_history
from core.repository import make_message


def test_trim_keeps_last_n():
    assert trim_history([1, 2, 3, 4, 5], 3) == [3, 4, 5]


def test_summary_sentences_scale_with_session_size():
    # Monotonic-ish: a bigger session never targets fewer sentences.
    assert summary_sentences(2) <= summary_sentences(12) <= summary_sentences(40)
    # Bounded: at least 1, never more than 8.
    assert summary_sentences(0) == 1
    assert summary_sentences(1) == 1
    assert summary_sentences(10_000) == 8
    # A short chat is a one-liner; a long one earns several sentences.
    assert summary_sentences(2) == 1
    assert summary_sentences(30) >= 5


def test_summary_request_targets_more_for_a_bigger_session():
    def msgs(n):
        return [
            make_message("s", "owner", "user" if i % 2 == 0 else "lili", f"line {i}")
            for i in range(n)
        ]

    small_sys, _ = summary_request(msgs(2))
    big_sys, _ = summary_request(msgs(30))
    # Both build on the stable base instruction…
    assert SUMMARY_SYSTEM in small_sys and SUMMARY_SYSTEM in big_sys
    # …but the bigger session asks for more sentences.
    assert f"{summary_sentences(2)} речень" in small_sys
    assert f"{summary_sentences(30)} речень" in big_sys
    assert summary_sentences(2) < summary_sentences(30)


def test_trim_exact_at_boundary():
    assert trim_history([1, 2, 3, 4], 4) == [1, 2, 3, 4]
    assert trim_history([1, 2, 3, 4], 5) == [1, 2, 3, 4]  # fewer than window → all


def test_trim_empty_and_zero():
    assert trim_history([], 5) == []
    assert trim_history([1, 2, 3], 0) == []
    assert trim_history([1, 2, 3], -1) == []


def test_trim_returns_a_new_list():
    src = [1, 2, 3]
    out = trim_history(src, 2)
    assert out == [2, 3]
    assert out is not src


def test_compaction_plan_floating_window():
    from core.memory import compaction_plan

    # No compaction until the live tail would exceed window + batch.
    assert compaction_plan(50, 0, 40, 20) == 0  # 50 live < 60 → keep all verbatim
    assert compaction_plan(40, 0, 40, 20) == 0  # exactly the window → nothing to do
    assert compaction_plan(59, 0, 40, 20) == 0  # just under the trigger
    # At window + batch, fold the oldest down to a window-length live tail.
    assert compaction_plan(60, 0, 40, 20) == 20  # fold 20, keep 40 verbatim
    assert compaction_plan(80, 20, 40, 20) == 40  # next batch folds another 20
    # Never goes backwards.
    assert compaction_plan(45, 30, 40, 20) >= 30

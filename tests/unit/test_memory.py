"""Unit tests for memory helpers (LUMI-008 windowing; summary scaling)."""

from core.agent import Core
from core.llm import MockLLMClient
from core.memory import SUMMARY_SYSTEM, summary_request, summary_sentences, trim_history
from core.repository import make_message
from state.local_store import JsonRepository


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


def test_core_sends_only_the_window_to_the_model(tmp_path):
    llm = MockLLMClient("ok")
    core = Core(
        llm=llm,
        repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.",
        model="m",
        memory_window=2,  # keep only the last 2 prior messages in context
    )
    session = core.start_session()
    core.reply("a", session)  # history: [] → sends just "a"
    core.reply("b", session)  # prior: [a-user, a-lili] → windowed to 2
    core.reply("c", session)  # prior: 4 msgs → windowed to last 2

    last = llm.calls[-1]["messages"]
    # 2 windowed prior messages + the new "c" line.
    assert len(last) == 3
    assert last[-1] == {"role": "user", "content": "c"}
    # Full history is still persisted (not trimmed in storage).
    assert len(core._repo.load_messages(session.id)) == 6

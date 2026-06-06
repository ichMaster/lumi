"""Unit tests for memory helpers (LUMI-008 windowing)."""

from core.agent import Core
from core.llm import MockLLMClient
from core.memory import trim_history
from state.local_store import JsonRepository


def test_trim_keeps_last_n():
    assert trim_history([1, 2, 3, 4, 5], 3) == [3, 4, 5]


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

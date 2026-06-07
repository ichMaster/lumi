"""Integration test for the core turn (LUMI-005).

A full turn `user_text → reply` against MockLLMClient (no paid call), asserting
the model is reached only via the LLMClient seam and both messages persist.
"""

from datetime import UTC, datetime

from core.agent import Core, build_core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from state.local_store import JsonRepository

# A fixed clock makes the per-message timestamps deterministic (v0.4).
_CLK = fixed_clock(datetime(2026, 6, 7, 14, 30, tzinfo=UTC))


def _core_with(tmp_path, llm):
    repo = JsonRepository(tmp_path / "store.json")
    return Core(
        llm=llm,
        repository=repo,
        canon="Ти — Лілі.",
        model="claude-haiku-4-5-20251001",
        clock=_CLK,
    ), repo


def test_full_turn_returns_reply_and_persists_both_messages(tmp_path):
    llm = MockLLMClient("Привіт. Я Лілі.")
    core, repo = _core_with(tmp_path, llm)

    session = core.start_session()
    out = core.reply("Привіт!", session)

    assert out.reply == "Привіт. Я Лілі."  # v0.3: reply() returns an EmotionState
    msgs = repo.load_messages(session.id)
    assert [(m.role, m.text) for m in msgs] == [
        ("user", "Привіт!"),
        ("lili", "Привіт. Я Лілі."),
    ]


def test_turn_sends_canon_and_history_to_the_model(tmp_path):
    llm = MockLLMClient(["перша", "друга"])
    core, _ = _core_with(tmp_path, llm)
    session = core.start_session()

    core.reply("раз", session)
    core.reply("два", session)

    # Second call must carry the canon as system + prior turns + the new line.
    # Лілі's prior reply is replayed with its <emotion> tag reconstructed (the mock
    # derives a calm/0.5 state) so the model keeps emitting the tag.
    second = llm.calls[1]
    assert second["system"].startswith("Ти — Лілі.")
    assert second["model"] == "claude-haiku-4-5-20251001"
    # Each message is prefixed with its date-time (v0.4); the assistant turn also
    # carries the reconstructed <emotion> tag.
    assert second["messages"] == [
        {"role": "user", "content": "[2026-06-07 14:30] раз"},
        {"role": "assistant", "content": "[2026-06-07 14:30] перша <emotion>calm 0.5</emotion>"},
        {"role": "user", "content": "[2026-06-07 14:30] два"},
    ]


def test_history_persists_across_a_restart(tmp_path):
    path = tmp_path / "store.json"
    llm = MockLLMClient("ага")
    core = Core(
        llm=llm,
        repository=JsonRepository(path),
        canon="Ти — Лілі.",
        model="m",
    )
    session = core.start_session()
    core.reply("привіт", session)

    # New core + store over the same file: history is still there.
    reopened = JsonRepository(path)
    msgs = reopened.load_messages(session.id)
    assert len(msgs) == 2


def test_build_core_wires_from_config_with_injected_llm_and_repo(tmp_path):
    # build_core never touches the Anthropic SDK when an llm is injected.
    llm = MockLLMClient("ok")
    repo = JsonRepository(tmp_path / "store.json")
    core = build_core(llm=llm, repository=repo)

    session = core.start_session()
    assert core.reply("hi", session).reply == "ok"
    # The canon (system prompt) was loaded from the configured path.
    assert "Лілі" in llm.calls[0]["system"]

"""Integration test for the core turn (LUMI-005).

A full turn `user_text → reply` against MockLLMClient (no paid call), asserting
the model is reached only via the LLMClient seam and both messages persist.
"""

from core.agent import Core, build_core
from core.llm import MockLLMClient
from state.local_store import JsonRepository


def _core_with(tmp_path, llm):
    repo = JsonRepository(tmp_path / "store.json")
    return Core(
        llm=llm,
        repository=repo,
        system_prompt="Ти — Лілі.",
        model="claude-haiku-4-5-20251001",
    ), repo


def test_full_turn_returns_reply_and_persists_both_messages(tmp_path):
    llm = MockLLMClient("Привіт. Я Лілі.")
    core, repo = _core_with(tmp_path, llm)

    session = core.start_session()
    out = core.reply("Привіт!", session)

    assert out == "Привіт. Я Лілі."
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
    second = llm.calls[1]
    assert second["system"] == "Ти — Лілі."
    assert second["model"] == "claude-haiku-4-5-20251001"
    assert second["messages"] == [
        {"role": "user", "content": "раз"},
        {"role": "assistant", "content": "перша"},
        {"role": "user", "content": "два"},
    ]


def test_history_persists_across_a_restart(tmp_path):
    path = tmp_path / "store.json"
    llm = MockLLMClient("ага")
    core = Core(
        llm=llm,
        repository=JsonRepository(path),
        system_prompt="Ти — Лілі.",
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
    assert core.reply("hi", session) == "ok"
    # The canon (system prompt) was loaded from the configured path.
    assert "Лілі" in llm.calls[0]["system"]

"""v0.19 LUMI-082 — the file tool wired into Core.reply (mock model, no paid calls)."""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 6, 15, 12, 0, tzinfo=UTC))
_STATE = {"reply": "прочитала", "emotion": "calm", "intensity": 0.5}


def _core(tmp_path, llm, *, file_tool=False, user="owner") -> Core:
    return Core(
        llm=llm, repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.", model="m", clock=_CLK, user_id=user,
        mood_enabled=False, closeness_enabled=False, thoughts_enabled=False,
        file_tool_enabled=file_tool, files_dir=tmp_path / "files",
        file_read_lines=10, file_find_max=5, tool_max_steps=4,
    )


def _sandbox(tmp_path, user, name, text):
    root = tmp_path / "files" / user
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text, encoding="utf-8")


def test_turn_reads_sandbox_file_when_on(tmp_path):
    _sandbox(tmp_path, "owner", "notes.md", "вступ\nРозділ 4: оплата і тарифи\nкінець\n")
    mock = MockLLMClient(states=_STATE, tool_script=[
        ("find_in_file", {"path": "notes.md", "query": "Розділ 4"}),
        ("read_file", {"path": "notes.md", "start_line": 2, "line_count": 1}),
    ])
    core = _core(tmp_path, mock, file_tool=True)
    state = core.reply("прочитай розділ про оплату", core.start_session())

    assert state.reply == "прочитала" and state.emotion.value == "calm"
    assert [c[0] for c in mock.tool_calls] == ["find_in_file", "read_file"]
    assert "line 2:" in mock.tool_calls[0][2] and "оплата" in mock.tool_calls[0][2]  # find → line number
    assert "Розділ 4: оплата і тарифи" in mock.tool_calls[1][2]  # read returned that line


def test_turn_offers_no_tools_when_off(tmp_path):
    mock = MockLLMClient(states={"reply": "ок", "emotion": "joy", "intensity": 0.8},
                         tool_script=[("read_file", {"path": "anything"})])
    core = _core(tmp_path, mock, file_tool=False)
    state = core.reply("привіт", core.start_session())
    assert state.emotion.value == "joy"
    assert mock.tool_calls == []  # executor never invoked — no tools offered when off


def test_executor_is_bound_to_the_active_users_sandbox(tmp_path):
    # Alice has a secret; Bob's turn (rooted at files/bob) must not reach it.
    _sandbox(tmp_path, "alice", "secret.md", "ALICE SECRET\n")
    mock = MockLLMClient(states=_STATE, tool_script=[("read_file", {"path": "secret.md"})])
    core_bob = _core(tmp_path, mock, file_tool=True, user="bob")
    core_bob.reply("read the secret", core_bob.start_session())

    result = mock.tool_calls[0][2]
    assert "ALICE SECRET" not in result and "file not found" in result  # bob's root, isolated

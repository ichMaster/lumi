"""v0.24 LUMI-100 — contract: send_image degrades on non-image/traversal/missing/no-sink (sink not
called), is per-user isolated, is absent when off, and the {reply, emotion, intensity} contract holds.

A fake telegram_sink (records calls) stands in for the TUI's outbox write — no real Telegram.
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.emotion import EmotionState
from core.llm import MockLLMClient
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 6, 17, 12, 0, tzinfo=UTC))
_CALM = {"reply": "ось", "emotion": "calm", "intensity": 0.5}


def _sink():
    calls: list[tuple[str, str]] = []

    def sink(abs_path: str, caption: str) -> None:
        calls.append((abs_path, caption))

    sink.calls = calls  # type: ignore[attr-defined]
    return sink


def _png(files_dir, user="owner", rel="art/cat.png", data=b"\x89PNG"):
    f = files_dir / user / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(data)
    return f


def _core(tmp_path, llm, *, image=True, sink=None, user="owner"):
    repo = JsonRepository(tmp_path / f"{user}.json")
    core = Core(
        llm=llm, repository=repo, canon="C", model="m", clock=_CLK, user_id=user,
        mood_enabled=False, closeness_enabled=False, thoughts_enabled=False,
        image_enabled=image, files_dir=tmp_path / "files", telegram_sink=sink, tool_max_steps=5,
    )
    return core, repo


# --- happy path: the sink gets the resolved path + caption ----------------------------------------
def test_send_calls_sink_with_resolved_path(tmp_path):
    _png(tmp_path / "files")
    sink = _sink()
    mock = MockLLMClient(states=_CALM, tool_script=[("send_image", {"path": "art/cat.png", "caption": "ось"})])
    core, _ = _core(tmp_path, mock, sink=sink)
    core.reply("надішли", core.start_session())
    assert sink.calls == [(str((tmp_path / "files" / "owner" / "art" / "cat.png").resolve()), "ось")]
    assert mock.tool_calls[0][2] == "sent cat.png to Telegram"


# --- degrade paths: the sink is NOT called, the turn completes ------------------------------------
def test_non_image_degrades_sink_not_called(tmp_path):
    (tmp_path / "files" / "owner").mkdir(parents=True)
    (tmp_path / "files" / "owner" / "note.txt").write_text("hi")
    sink = _sink()
    mock = MockLLMClient(states=_CALM, tool_script=[("send_image", {"path": "note.txt"})])
    core, _ = _core(tmp_path, mock, sink=sink)
    state = core.reply("надішли", core.start_session())
    assert isinstance(state, EmotionState) and "not an image" in mock.tool_calls[0][2]
    assert sink.calls == []


def test_traversal_degrades_sink_not_called(tmp_path):
    _png(tmp_path, user="", rel="secret.png")  # tmp_path/secret.png — outside the sandbox
    sink = _sink()
    mock = MockLLMClient(states=_CALM, tool_script=[("send_image", {"path": "../../secret.png"})])
    core, _ = _core(tmp_path, mock, sink=sink)
    state = core.reply("надішли", core.start_session())
    assert isinstance(state, EmotionState) and "traversal" in mock.tool_calls[0][2]
    assert sink.calls == []


def test_missing_file_degrades_sink_not_called(tmp_path):
    sink = _sink()
    mock = MockLLMClient(states=_CALM, tool_script=[("send_image", {"path": "art/nope.png"})])
    core, _ = _core(tmp_path, mock, sink=sink)
    state = core.reply("надішли", core.start_session())
    assert isinstance(state, EmotionState) and "not found" in mock.tool_calls[0][2]
    assert sink.calls == []


def test_no_sink_reports_not_connected(tmp_path):
    _png(tmp_path / "files")
    mock = MockLLMClient(states=_CALM, tool_script=[("send_image", {"path": "art/cat.png"})])
    core, _ = _core(tmp_path, mock, sink=None)  # bridge off
    state = core.reply("надішли", core.start_session())
    assert isinstance(state, EmotionState) and "Telegram not connected" in mock.tool_calls[0][2]


# --- per-user isolation: A can never reach B's sandbox --------------------------------------------
def test_send_per_user_isolation(tmp_path):
    _png(tmp_path / "files", user="bob", rel="art/secret.png", data=b"BOB")
    sink = _sink()
    # alice asks to send "bob/art/secret.png" — under HER sandbox that resolves to files/alice/bob/...,
    # which doesn't exist; bob's real file is never reached.
    mock = MockLLMClient(states=_CALM, tool_script=[("send_image", {"path": "bob/art/secret.png"})])
    core_alice, _ = _core(tmp_path, mock, sink=sink, user="alice")
    state = core_alice.reply("надішли", core_alice.start_session())
    assert isinstance(state, EmotionState) and "not found" in mock.tool_calls[0][2]
    assert sink.calls == []  # bob's file was never sent


# --- off + the emotion contract -------------------------------------------------------------------
def test_off_no_send_tool(tmp_path):
    _png(tmp_path / "files")
    mock = MockLLMClient(states=_CALM, tool_script=[("send_image", {"path": "art/cat.png"})])
    core, _ = _core(tmp_path, mock, image=False, sink=_sink())
    core.reply("надішли", core.start_session())
    assert mock.tool_calls == []  # off → send_image not offered


def test_emotion_contract_holds_with_send(tmp_path):
    _png(tmp_path / "files")
    sink = _sink()
    mock = MockLLMClient(states={"reply": "тримай 🌸", "emotion": "tender", "intensity": 0.6},
                         tool_script=[("send_image", {"path": "art/cat.png", "caption": "тримай"})])
    core, repo = _core(tmp_path, mock, sink=sink)
    session = core.start_session()
    state = core.reply("надішли малюнок", session)
    assert isinstance(state, EmotionState) and state.emotion.value == "tender" and 0 <= state.intensity <= 1
    assert [m.role for m in repo.load_messages(session.id)] == ["user", "lili"]  # both persisted
    assert len(sink.calls) == 1  # the picture was sent once

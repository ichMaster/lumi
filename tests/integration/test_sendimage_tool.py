"""v0.24 LUMI-099 — send_image wired through Core.reply + the TUI photo sink (no real Telegram).

A full turn: the mock model calls ``send_image`` → the core invokes the injected ``telegram_sink`` → the
TUI's :func:`make_photo_sink` appends a **photo record** to a real outbox file (the single writer). The
``{reply, emotion, intensity}`` contract still validates.
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.emotion import EmotionState
from core.llm import MockLLMClient
from state import fifo
from state.local_store import JsonRepository
from tui.bridge import make_photo_sink

_CLK = fixed_clock(datetime(2026, 6, 17, 12, 0, tzinfo=UTC))
_STATE = {"reply": "ось, тримай 🌸", "emotion": "tender", "intensity": 0.6}


def _png(files_dir, user="owner", rel="art/cat.png", data=b"\x89PNG-bytes"):
    f = files_dir / user / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(data)
    return f


def _core(tmp_path, llm, *, image=True, sink=None, user="owner"):
    return Core(
        llm=llm, repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.", model="m", clock=_CLK, user_id=user,
        mood_enabled=False, closeness_enabled=False, thoughts_enabled=False,
        image_enabled=image, files_dir=tmp_path / "files", telegram_sink=sink, tool_max_steps=5,
    )


def test_turn_sends_image_appends_a_photo_record(tmp_path):
    _png(tmp_path / "files")
    outbox = tmp_path / "outbox.jsonl"
    sink = make_photo_sink(outbox)  # the real TUI single-writer sink
    mock = MockLLMClient(
        states=_STATE,
        tool_script=[("send_image", {"path": "art/cat.png", "caption": "глянь, що я зробила"})],
    )
    core = _core(tmp_path, mock, sink=sink)
    state = core.reply("надішли мені малюнок", core.start_session())

    assert isinstance(state, EmotionState) and state.emotion.value == "tender"
    assert mock.tool_calls[0][0] == "send_image" and mock.tool_calls[0][2] == "sent cat.png to Telegram"
    recs = fifo.read_since(outbox, 0)
    assert len(recs) == 1
    rec = recs[0]
    assert rec["kind"] == "lili" and rec["text"] == "глянь, що я зробила"
    assert rec["photo"] == str((tmp_path / "files" / "owner" / "art" / "cat.png").resolve())


def test_turn_send_image_no_bridge_reports_not_connected(tmp_path):
    _png(tmp_path / "files")
    mock = MockLLMClient(states=_STATE, tool_script=[("send_image", {"path": "art/cat.png"})])
    core = _core(tmp_path, mock, sink=None)  # bridge off → no sink
    state = core.reply("надішли", core.start_session())
    assert isinstance(state, EmotionState)
    assert "Telegram not connected" in mock.tool_calls[0][2]


def test_no_send_image_when_image_off(tmp_path):
    _png(tmp_path / "files")
    outbox = tmp_path / "outbox.jsonl"
    mock = MockLLMClient(states=_STATE, tool_script=[("send_image", {"path": "art/cat.png"})])
    core = _core(tmp_path, mock, image=False, sink=make_photo_sink(outbox))
    core.reply("надішли", core.start_session())
    assert mock.tool_calls == []  # off → send_image not offered
    assert not outbox.exists()  # nothing written

"""v0.26 LUMI-106 — the voice-in path end-to-end: mic → STT → inbox → a turn identical to typed.

Simulates the dictator (recognize_and_append, mock STT) + the TUI inbox drain (drain_inbox_records) +
the core, with no Textual app and no audio. Pins: identical-to-typed, empty-drop, dedup, and that
dictation is independent of the Telegram bridge. STT mocked — no audio, no network, no paid calls.
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.emotion import EmotionState
from core.llm import MockLLMClient
from state import fifo
from state.local_store import JsonRepository
from tui.bridge import drain_inbox_records
from voice.dictator import recognize_and_append
from voice.stt import MockSTT

_CLK = fixed_clock(datetime(2026, 6, 17, 12, 0, tzinfo=UTC))
_STATE = {"reply": "о, привіт!", "emotion": "joy", "intensity": 0.8}


def _core(tmp_path, name="s"):
    return Core(
        llm=MockLLMClient(states=_STATE), repository=JsonRepository(tmp_path / f"{name}.json"),
        canon="Ти — Лілі.", model="m", clock=_CLK,
        mood_enabled=False, closeness_enabled=False, thoughts_enabled=False,
    )


def _drain_turns(core, session, inbox, pos):
    """The TUI drain loop, distilled: each unread inbox line runs as a turn; return the states."""
    states = []
    for rec in drain_inbox_records(inbox, pos):
        states.append((rec, core.reply(rec["text"], session)))
    return states


# --- a recognized line drives a turn IDENTICAL to a typed one --------------------------------------
def test_dictated_line_drives_a_turn_like_typed(tmp_path):
    inbox, pos = tmp_path / "inbox.jsonl", tmp_path / "inbox.pos"
    core = _core(tmp_path)
    session = core.start_session()

    # the dictator: mic → STT → inbox
    rid = recognize_and_append(b"<audio>", inbox, MockSTT("привіт"))
    assert rid == 1

    # the TUI drain → a turn
    turns = _drain_turns(core, session, inbox, pos)
    assert len(turns) == 1
    rec, state = turns[0]
    assert rec["text"] == "привіт" and rec["source"] == "voice"   # tagged voice (reference only)
    assert isinstance(state, EmotionState) and state.emotion.value == "joy"

    # IDENTICAL to typing it: the same text typed yields the same reply (the core has no voice path)
    typed = _core(tmp_path, "typed")
    typed_state = typed.reply("привіт", typed.start_session())
    assert (state.reply, state.emotion, state.intensity) == (typed_state.reply, typed_state.emotion, typed_state.intensity)


# --- empty / low-confidence recognition → no inbox record, no turn ---------------------------------
def test_empty_recognition_no_record_no_turn(tmp_path):
    inbox, pos = tmp_path / "inbox.jsonl", tmp_path / "inbox.pos"
    core = _core(tmp_path)
    session = core.start_session()

    assert recognize_and_append(b"<audio>", inbox, MockSTT("")) is None     # empty
    assert recognize_and_append(b"<audio>", inbox, MockSTT("   ")) is None   # whitespace only
    assert fifo.read_since(inbox, 0) == []                                   # nothing written
    assert _drain_turns(core, session, inbox, pos) == []                     # → no turn


# --- dedup: a drained line is consumed once (the inbox pointer advances) ---------------------------
def test_dedup_line_consumed_once(tmp_path):
    inbox, pos = tmp_path / "inbox.jsonl", tmp_path / "inbox.pos"
    core = _core(tmp_path)
    session = core.start_session()

    recognize_and_append(b"a", inbox, MockSTT("раз"))
    first = _drain_turns(core, session, inbox, pos)
    assert [r["text"] for r, _ in first] == ["раз"]
    assert _drain_turns(core, session, inbox, pos) == []  # caught up — not doubled

    recognize_and_append(b"b", inbox, MockSTT("два"))
    again = _drain_turns(core, session, inbox, pos)
    assert [r["text"] for r, _ in again] == ["два"]  # only the new line


# --- dictation is independent of the Telegram bridge ----------------------------------------------
def test_dictation_independent_of_bridge(tmp_path):
    # no bridge anywhere — the inbox path is fed only by the dictator and drained the same way.
    inbox, pos = tmp_path / "inbox.jsonl", tmp_path / "inbox.pos"
    core = _core(tmp_path)
    session = core.start_session()
    recognize_and_append(b"<audio>", inbox, MockSTT("як справи?"))
    turns = _drain_turns(core, session, inbox, pos)
    assert len(turns) == 1 and isinstance(turns[0][1], EmotionState)


# --- contract: the core persists a dictated turn like any other -----------------------------------
def test_emotion_contract_and_persistence_with_dictation(tmp_path):
    inbox, pos = tmp_path / "inbox.jsonl", tmp_path / "inbox.pos"
    repo = JsonRepository(tmp_path / "s.json")
    core = Core(
        llm=MockLLMClient(states=_STATE), repository=repo, canon="C", model="m", clock=_CLK,
        mood_enabled=False, closeness_enabled=False, thoughts_enabled=False,
    )
    session = core.start_session()
    recognize_and_append(b"<audio>", inbox, MockSTT("розкажи щось"))
    _, state = _drain_turns(core, session, inbox, pos)[0]
    assert isinstance(state, EmotionState) and 0 <= state.intensity <= 1
    assert [m.role for m in repo.load_messages(session.id)] == ["user", "lili"]  # both persisted

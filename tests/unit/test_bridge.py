"""TUI bridge — drain inbox, mirror only Лілі's reply to outbox (v0.13, LUMI-054)."""

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from state import fifo
from state.local_store import JsonRepository
from tui.bridge import drain_inbox, mirror_reply, mirror_user

_DAY = fixed_clock(datetime(2026, 6, 10, 14, 0, tzinfo=UTC))


# --- drain_inbox ----------------------------------------------------------
def test_drain_inbox_returns_new_and_advances(tmp_path):
    inbox, pos = tmp_path / "inbox.jsonl", tmp_path / "inbox.pos"
    fifo.append(inbox, "привіт")
    fifo.append(inbox, "як справи")
    assert drain_inbox(inbox, pos) == ["привіт", "як справи"]
    assert drain_inbox(inbox, pos) == []  # caught up (pointer advanced)
    fifo.append(inbox, "ще")
    assert drain_inbox(inbox, pos) == ["ще"]  # only the new one


# --- mirror_reply ---------------------------------------------------------
def test_mirror_reply_writes_her_reply(tmp_path):
    from core.emotion import Emotion, EmotionState
    outbox = tmp_path / "outbox.jsonl"
    state = EmotionState(reply="о, привіт!", emotion=Emotion.JOY, intensity=0.8)
    mirror_reply(outbox, state)
    rec = fifo.read_since(outbox, 0)[0]
    assert rec["text"] == "о, привіт!" and rec["kind"] == "lili"
    assert rec["emotion"] == "joy" and rec["intensity"] == 0.8


def test_mirror_user_writes_your_line(tmp_path):
    # your keyboard line → outbox as kind="user" (so it shows in Telegram, distinct from her reply)
    outbox = tmp_path / "outbox.jsonl"
    mirror_user(outbox, "як ти?")
    rec = fifo.read_since(outbox, 0)[0]
    assert rec["text"] == "як ти?" and rec["kind"] == "user" and "emotion" not in rec


# --- the inbox → turn → outbox flow (no echo) -----------------------------
def test_inbox_to_turn_to_outbox_is_echo_free(tmp_path):
    inbox, pos = tmp_path / "inbox.jsonl", tmp_path / "inbox.pos"
    outbox = tmp_path / "outbox.jsonl"
    core = Core(
        llm=MockLLMClient(states={"reply": "о, привіт!", "emotion": "joy", "intensity": 0.8}),
        repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m",
        clock=_DAY, mood_enabled=False, thoughts_enabled=False,
    )
    s = core.start_session()
    fifo.append(inbox, "привіт")  # a Telegram message lands in the inbox

    for text in drain_inbox(inbox, pos):       # the TUI's inbox path…
        state = core.reply(text, s)            # …runs it as a turn…
        mirror_reply(outbox, state)            # …and mirrors ONLY her reply

    out = fifo.read_since(outbox, 0)
    assert [r["text"] for r in out] == ["о, привіт!"]  # her reply only
    assert "привіт" not in [r["text"] for r in out]    # NOT the user's input → no echo
    assert drain_inbox(inbox, pos) == []               # the inbox is consumed

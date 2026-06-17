"""v0.26 LUMI-105 — the TUI dictation helpers: listen.flag + the source-aware inbox drain.

Pure bridge helpers (no Textual): set_listen_flag writes on/off; drain_inbox_records carries the
record's source so the TUI can voice-mark a dictated line. The full-turn TUI wiring is covered by the
integration suite (LUMI-106).
"""
from __future__ import annotations

from state import fifo
from tui.bridge import drain_inbox, drain_inbox_records, set_listen_flag
from voice.dictator import read_flag


# --- set_listen_flag (the TUI is the sole writer; the dictator reads it) ---------------------------
def test_set_listen_flag_round_trips(tmp_path):
    flag = tmp_path / "listen.flag"
    set_listen_flag(flag, True)
    assert flag.read_text() == "on" and read_flag(flag) is True
    set_listen_flag(flag, False)
    assert flag.read_text() == "off" and read_flag(flag) is False


def test_set_listen_flag_creates_parent_dir(tmp_path):
    flag = tmp_path / "state" / "listen.flag"  # parent does not exist yet
    set_listen_flag(flag, True)
    assert flag.is_file() and read_flag(flag) is True


# --- drain_inbox_records carries source (voice vs telegram); drain_inbox stays text-only -----------
def test_drain_inbox_records_carries_source(tmp_path):
    inbox, pos = tmp_path / "inbox.jsonl", tmp_path / "inbox.pos"
    fifo.append(inbox, "привіт", source="voice")        # a dictated line
    fifo.append(inbox, "from telegram")                  # a Telegram line (no source)
    recs = drain_inbox_records(inbox, pos)
    assert [r["text"] for r in recs] == ["привіт", "from telegram"]
    assert recs[0].get("source") == "voice" and recs[1].get("source") is None
    assert drain_inbox_records(inbox, pos) == []          # caught up (pointer advanced)


def test_drain_inbox_text_only_unchanged(tmp_path):
    inbox, pos = tmp_path / "inbox.jsonl", tmp_path / "inbox.pos"
    fifo.append(inbox, "як справи", source="voice")
    assert drain_inbox(inbox, pos) == ["як справи"]       # the v0.13 text-only contract is unchanged

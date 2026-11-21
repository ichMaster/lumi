"""v5.1 LUMI-341 contract — the media and the tool record shapes (ARCHITECTURE §Contracts, "Usage records").

A changed shape must change this test and ARCHITECTURE in the same commit. No field holds a message, a prompt,
audio, a tool's result or a secret: the only free text a record carries is the tool's bounded input line."""

from __future__ import annotations

import json

from core import ledgers
from core.agent import TOOL_INPUT_CHARS

MEDIA_KEYS = {"ts", "provider", "service", "model", "units", "latency_ms", "outcome", "origin", "channel"}
TOOL_KEYS = {"ts", "tool", "outcome", "ms", "result_chars", "turn_id", "session_id", "origin", "channel", "input"}


def test_the_media_record_shape(tmp_path):
    path = tmp_path / ledgers.MEDIA_LEDGER
    ledgers.append_media(path, ledgers.media_record(provider="google", service="image", model="m",
                                                    units={"images": 1}, latency_ms=10, outcome="ok"))
    ledgers.append_media(path, ledgers.media_record(provider="deepgram", service="stt", model="nova-3",
                                                    units={"audio_s": 2.0}, latency_ms=10, outcome="ok", mode="batch"))
    image, stt = ledgers.read_records(path)
    assert set(image) == MEDIA_KEYS
    assert set(stt) == MEDIA_KEYS | {"mode"}  # only a recognition carries its mode
    assert set(ledgers.MEDIA_FIELDS) == MEDIA_KEYS | {"mode"}
    assert ledgers.SERVICES == ("tts", "stt", "image", "search")
    assert set(ledgers.UNIT_KEYS) == {"chars", "audio_s", "images", "requests"}


def test_the_tool_record_shape(tmp_path):
    path = tmp_path / ledgers.TOOLS_LEDGER
    ledgers.append_tool(path, ledgers.ToolRecord(ts="t", tool="read_file", outcome="ok", ms=3, result_chars=12,
                                                 turn_id="2026-11-21T12:00:00+00:00", session_id="s", origin="turn",
                                                 channel="window", input="path=notes.md"))
    (rec,) = ledgers.read_records(path)
    assert set(rec) == TOOL_KEYS == set(ledgers.TOOL_FIELDS)
    assert isinstance(rec["ms"], int) and isinstance(rec["result_chars"], int)


def test_the_only_free_text_is_the_bounded_input_line():
    line = ledgers.input_line({"query": "x" * 1000}, TOOL_INPUT_CHARS)
    assert len(line) <= TOOL_INPUT_CHARS
    # every other value a writer can put in a record is a name, a count or an outcome word
    rec = ledgers.media_record(provider="elevenlabs", service="tts", model="eleven_flash_v2_5",
                               units={"chars": 10}, latency_ms=1, outcome=ledgers.error_outcome(RuntimeError("Секрет")))
    assert "Секрет" not in json.dumps(rec.__dict__, ensure_ascii=False)
    assert ledgers.error_outcome(ValueError("sk-live-123")) == "error:ValueError"


def test_the_ledgers_live_in_the_data_root_by_their_names():
    assert (ledgers.MEDIA_LEDGER, ledgers.TOOLS_LEDGER) == ("media-ledger.jsonl", "tools.jsonl")

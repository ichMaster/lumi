"""v0.26 LUMI-104 — the /voice STT adapter + the dictator helpers (no audio, no network).

MockSTT returns canned text; the cloud/offline adapters import without their SDK (lazy). The dictator's
recognize_and_append appends a voice line to inbox, drops empty/low-confidence, and dedups via the fifo id.
"""
from __future__ import annotations

import pytest

from state import fifo
from voice.dictator import read_flag, recognize_and_append
from voice.stt import (
    DeepgramSTT,
    ElevenLabsScribeSTT,
    MockSTT,
    WhisperSTT,
    _deepgram_transcript,
    build_stt,
)


# --- the STT seam -----------------------------------------------------------------------------------
def test_import_and_construct_without_any_sdk():
    # the SDK imports are lazy (inside recognize) → the adapters construct with no STT dep installed
    assert DeepgramSTT("k", model="nova-3").model == "nova-3"
    assert ElevenLabsScribeSTT("k").model == "scribe_v1"
    assert WhisperSTT(model="base").model == "base"


def test_mock_stt_returns_canned_and_records():
    stt = MockSTT("привіт")
    assert stt.recognize(b"\x00\x01\x02", lang="uk") == "привіт"
    assert stt.calls == [(3, "uk")]


def test_build_stt_picks_provider():
    assert isinstance(build_stt("deepgram", api_key="k"), DeepgramSTT)
    assert isinstance(build_stt("elevenlabs", api_key="k"), ElevenLabsScribeSTT)
    assert isinstance(build_stt("whisper"), WhisperSTT)
    assert isinstance(build_stt("mock"), MockSTT)
    with pytest.raises(ValueError):
        build_stt("nonsense")


def test_build_stt_model_override():
    assert build_stt("deepgram", api_key="k").model == "nova-3"          # default
    assert build_stt("deepgram", api_key="k", model="nova-2").model == "nova-2"  # overridden
    assert build_stt("deepgram", api_key="k", model="").model == "nova-3"  # "" → default


def test_deepgram_transcript_parse():
    ok = {"results": {"channels": [{"alternatives": [{"transcript": "привіт світ", "confidence": 0.98}]}]}}
    assert _deepgram_transcript(ok) == "привіт світ"
    # silence / malformed shapes → "" (so recognize_and_append drops them), never a KeyError
    assert _deepgram_transcript({"results": {"channels": [{"alternatives": [{"transcript": ""}]}]}}) == ""
    assert _deepgram_transcript({}) == ""
    assert _deepgram_transcript({"results": {"channels": []}}) == ""


# --- read_flag --------------------------------------------------------------------------------------
def test_read_flag_on_off(tmp_path):
    flag = tmp_path / "listen.flag"
    assert read_flag(flag) is False  # missing → off
    flag.write_text("on")
    assert read_flag(flag) is True
    flag.write_text("OFF")
    assert read_flag(flag) is False
    flag.write_text("  on \n")
    assert read_flag(flag) is True  # whitespace tolerated


# --- recognize_and_append ---------------------------------------------------------------------------
def test_recognize_appends_a_voice_line(tmp_path):
    inbox = tmp_path / "inbox.jsonl"
    rid = recognize_and_append(b"<audio>", inbox, MockSTT("як справи?"))
    assert rid == 1
    recs = fifo.read_since(inbox, 0)
    assert len(recs) == 1
    assert recs[0]["text"] == "як справи?" and recs[0]["source"] == "voice"


def test_empty_recognition_writes_nothing(tmp_path):
    inbox = tmp_path / "inbox.jsonl"
    assert recognize_and_append(b"<audio>", inbox, MockSTT("")) is None      # empty
    assert recognize_and_append(b"<audio>", inbox, MockSTT("   ")) is None    # whitespace only
    assert fifo.read_since(inbox, 0) == []  # nothing written


def test_recognition_error_degrades(tmp_path):
    class _Boom:
        def recognize(self, audio, *, lang="uk"):
            raise RuntimeError("stt down")
    inbox = tmp_path / "inbox.jsonl"
    assert recognize_and_append(b"<audio>", inbox, _Boom()) is None  # never raises
    assert fifo.read_since(inbox, 0) == []


def test_ids_are_monotonic_for_dedup(tmp_path):
    inbox = tmp_path / "inbox.jsonl"
    a = recognize_and_append(b"1", inbox, MockSTT("раз"))
    b = recognize_and_append(b"2", inbox, MockSTT("два"))
    assert (a, b) == (1, 2)  # the fifo counter gives each line a fresh id (consumer dedups by pointer)


def test_config_dictation_defaults():
    from core.config import (
        Config,  # the dataclass defaults (hermetic — not the developer's live .env)
    )
    c = Config()
    assert c.dictation is False and c.stt_provider == "deepgram"
    assert c.stt_model == "" and c.stt_lang == "uk" and c.listen_flag_path.name == "listen.flag"

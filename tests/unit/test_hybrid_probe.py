"""The hybrid probe's pure helpers — session payload (text out), text-delta dispatcher, stats.

No network, no audio, no paid calls; run() is glue and uncovered by design (as the sibling probes).
"""
from __future__ import annotations

from scripts.hybrid_probe import (
    DEFAULT_MODEL,
    HybridDispatcher,
    HybridStats,
    build_hybrid_session,
    parse_cli,
)


def test_parse_cli_defaults_and_overrides():
    d = parse_cli([])
    assert d["model"] is None and d["tts_model"] is None
    d = parse_cli(["--model", "gpt-realtime-2.1-mini", "--tts-model", "eleven_flash_v2_5"])
    assert (d["model"], d["tts_model"]) == ("gpt-realtime-2.1-mini", "eleven_flash_v2_5")
    assert DEFAULT_MODEL == "gpt-realtime-2.1"          # the flagship brain by default


def test_session_payload_is_text_out_with_ukrainian_asr():
    p = build_hybrid_session("Ти — Лілі.")
    s = p["session"]
    assert s["output_modalities"] == ["text"]           # ElevenLabs owns the sound
    assert "output" not in s["audio"]                   # no built-in voice at all
    assert s["instructions"].startswith("[ГОЛОСОВИЙ РЕЖИМ")  # the no-tags spoken style LEADS
    assert "Ти — Лілі." in s["instructions"]
    assert s["audio"]["input"]["transcription"]["language"] == "uk"
    assert s["audio"]["input"]["turn_detection"]["type"] == "semantic_vad"


def _dispatcher(clock):
    got = {"delta": [], "done": [], "user": [], "barge": 0, "first": []}
    d = HybridDispatcher(
        now=lambda: clock[0],
        on_text_delta=got["delta"].append,
        on_text_done=got["done"].append,
        on_user_transcript=got["user"].append,
        on_barge_in=lambda: got.__setitem__("barge", got["barge"] + 1),
        on_first_text=got["first"].append,
    )
    return d, got


def test_llm_first_latency_from_speech_stop_to_first_text_delta():
    clock = [10.0]
    d, got = _dispatcher(clock)
    d.feed({"type": "input_audio_buffer.speech_stopped"})
    clock[0] = 10.9
    d.feed({"type": "response.output_text.delta", "delta": "Прив"})
    clock[0] = 11.0
    d.feed({"type": "response.output_text.delta", "delta": "іт."})  # later deltas don't re-stamp
    assert [round(x, 2) for x in d.latencies] == [0.9]
    assert got["first"] and round(got["first"][0], 2) == 0.9
    assert got["delta"] == ["Прив", "іт."]
    assert d.speech_stopped_at == 10.0                  # exposed as the turn's total-clock t0


def test_text_done_assembles_deltas_when_done_text_is_empty():
    d, got = _dispatcher([0.0])
    d.feed({"type": "response.output_text.delta", "delta": "Привіт"})
    d.feed({"type": "response.output_text.delta", "delta": "!"})
    d.feed({"type": "response.output_text.done", "text": ""})
    assert got["done"] == ["Привіт!"]
    d.feed({"type": "response.text.delta", "delta": "ще"})           # the older event names work too
    d.feed({"type": "response.text.done", "text": "ще"})
    assert got["done"] == ["Привіт!", "ще"]


def test_user_transcript_barge_in_and_unknown_collection():
    d, got = _dispatcher([0.0])
    d.feed({"type": "conversation.item.input_audio_transcription.completed", "transcript": "привіт"})
    d.feed({"type": "input_audio_buffer.speech_started"})
    d.feed({"type": "response.shiny.new_event"})
    d.feed({"type": "response.shiny.new_event"})                     # collected once
    assert got["user"] == ["привіт"] and got["barge"] == 1
    assert d.unknown == ["response.shiny.new_event"]


def test_stats_summary_total_and_stage_medians():
    s = HybridStats()
    assert s.summary() == {"turns": 0}
    s.add(llm=0.9, tts=0.3, total=1.2)
    s.add(llm=1.1, tts=0.5, total=1.6)
    s.add(llm=1.0, tts=0.4, total=1.4)
    out = s.summary()
    assert (out["turns"], out["median_s"], out["min_s"], out["max_s"]) == (3, 1.4, 1.2, 1.6)
    assert out["stages_median_s"] == {"llm_first": 1.0, "tts_first": 0.4}

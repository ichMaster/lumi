"""v1.6.1 LUMI-197 — the realtime probe's pure helpers: session config, dispatcher, latency math.

No network, no audio hardware, no paid calls — the dispatcher is fed scripted event dicts with a
fake clock; run() is glue and stays uncovered by design.
"""
from __future__ import annotations

import base64

from scripts.realtime_probe import (
    KNOWN_VOICES,
    ProbeDispatcher,
    b64_pcm,
    build_session_update,
    parse_cli,
    pcm_from_b64,
)


def test_session_update_payload_shape():
    p = build_session_update("Ти — Лілі.", voice="cedar")
    assert p["type"] == "session.update"
    s = p["session"]
    assert s["instructions"] == "Ти — Лілі."                       # pure context rides instructions
    assert s["audio"]["output"]["voice"] == "cedar"                 # voice set BEFORE first audio
    assert s["audio"]["input"]["turn_detection"]["type"] == "semantic_vad"
    assert s["audio"]["input"]["turn_detection"]["interrupt_response"] is True
    assert s["audio"]["input"]["format"]["rate"] == 24_000
    assert s["output_modalities"] == ["audio"]


def test_parse_cli_voice_positional_flag_and_model():
    assert parse_cli([])["voice"] is None                       # env/default decides
    assert parse_cli(["cedar"])["voice"] == "cedar"              # positional
    assert parse_cli(["--voice", "sage"])["voice"] == "sage"     # flag
    assert parse_cli(["cedar", "--model", "gpt-realtime-2.1"])["model"] == "gpt-realtime-2.1"
    assert "marin" in KNOWN_VOICES and "cedar" in KNOWN_VOICES


def test_pcm_base64_round_trip():
    chunk = b"\x00\x01\x02\xff" * 100
    assert pcm_from_b64(b64_pcm(chunk)) == chunk


def _dispatcher(clock):
    got = {"audio": [], "user": [], "lili": [], "barge": 0, "first": []}
    d = ProbeDispatcher(
        now=lambda: clock[0],
        on_audio=got["audio"].append,
        on_user_transcript=got["user"].append,
        on_assistant_transcript=got["lili"].append,
        on_barge_in=lambda: got.__setitem__("barge", got["barge"] + 1),
        on_first_audio=got["first"].append,
    )
    return d, got


def _audio_event(data: bytes, etype="response.output_audio.delta"):
    return {"type": etype, "delta": base64.b64encode(data).decode()}


def test_latency_from_speech_stop_to_first_audio():
    clock = [100.0]
    d, got = _dispatcher(clock)
    d.feed({"type": "input_audio_buffer.speech_stopped"})     # he stopped speaking at t=100
    clock[0] = 100.8
    d.feed(_audio_event(b"aa"))                                # first audio delta at t=100.8
    clock[0] = 101.0
    d.feed(_audio_event(b"bb"))                                # later deltas don't re-stamp
    assert d.latencies == [0.8000000000000114] or round(d.latencies[0], 2) == 0.8
    assert got["first"] and round(got["first"][0], 2) == 0.8
    assert got["audio"] == [b"aa", b"bb"]                      # audio flows to the speaker


def test_transcripts_route_to_callbacks():
    d, got = _dispatcher([0.0])
    d.feed({"type": "conversation.item.input_audio_transcription.completed", "transcript": "привіт"})
    d.feed({"type": "response.output_audio_transcript.delta", "delta": "Прив"})
    d.feed({"type": "response.output_audio_transcript.delta", "delta": "іт!"})
    d.feed({"type": "response.output_audio_transcript.done", "transcript": ""})
    assert got["user"] == ["привіт"]
    assert got["lili"] == ["Привіт!"]                          # assembled from deltas when done is empty


def test_legacy_event_names_are_handled_too():
    d, got = _dispatcher([0.0])
    d.feed(_audio_event(b"xx", etype="response.audio.delta"))   # the older name
    d.feed({"type": "response.audio_transcript.done", "transcript": "готово"})
    assert got["audio"] == [b"xx"] and got["lili"] == ["готово"]


def test_barge_in_stops_playback_only_while_playing():
    d, got = _dispatcher([0.0])
    d.feed({"type": "input_audio_buffer.speech_started"})       # not playing → no barge-in
    assert got["barge"] == 0
    d.feed(_audio_event(b"aa"))                                 # she is speaking now
    d.feed({"type": "input_audio_buffer.speech_started"})       # he interrupts
    assert got["barge"] == 1


def test_summary_median_and_unknown_events_collected():
    clock = [0.0]
    d, _ = _dispatcher(clock)
    for latency in (1.0, 3.0, 2.0):
        d.feed({"type": "input_audio_buffer.speech_stopped"})
        clock[0] += latency
        d.feed(_audio_event(b"a"))
    s = d.summary()
    assert (s["turns"], s["median_s"]) == (3, 2.0)
    d.feed({"type": "response.shiny.new_event"})
    d.feed({"type": "response.shiny.new_event"})                # collected once
    assert d.unknown == ["response.shiny.new_event"]


def test_empty_session_summary():
    d, _ = _dispatcher([0.0])
    assert d.summary() == {"turns": 0}

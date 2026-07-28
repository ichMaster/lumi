"""The streaming-chain probe's pure helpers — the Deepgram WS URL, the utterance tracker, stats.

No network, no audio, no paid calls; run() is glue and uncovered by design (as the sibling probes).
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from scripts.chain_ws_probe import (
    DEFAULT_ENDPOINT_MS,
    UtteranceTracker,
    WsStats,
    build_deepgram_url,
    parse_cli,
)


def test_parse_cli_defaults_and_endpoint_override():
    d = parse_cli([])
    assert d["model"] is None and d["endpoint_ms"] == DEFAULT_ENDPOINT_MS
    d = parse_cli(["--model", "gemini-2.5-flash", "--endpoint-ms", "500"])
    assert (d["model"], d["endpoint_ms"]) == ("gemini-2.5-flash", 500)


def test_deepgram_url_streams_linear16_with_endpointing():
    url = urlparse(build_deepgram_url(model="nova-3", lang="uk", endpoint_ms=300))
    q = parse_qs(url.query)
    assert url.scheme == "wss" and url.netloc == "api.deepgram.com"
    assert q["model"] == ["nova-3"] and q["language"] == ["uk"]
    assert q["encoding"] == ["linear16"] and q["sample_rate"] == ["16000"]
    assert q["endpointing"] == ["300"]              # the server VAD ends the turn — no local hangover
    assert q["interim_results"] == ["true"]         # utterance_end needs interims flowing
    assert q["utterance_end_ms"] == ["1000"]


def _final(text: str, *, speech_final: bool = False) -> dict:
    return {"type": "Results", "is_final": True, "speech_final": speech_final,
            "channel": {"alternatives": [{"transcript": text}]}}


def test_utterance_flushes_on_speech_final_joining_segments():
    t = UtteranceTracker()
    assert t.feed({"type": "Results", "is_final": False,
                   "channel": {"alternatives": [{"transcript": "прив"}]}}) is None  # interim — noise
    assert t.feed(_final("Привіт, як")) is None                # a finalized SEGMENT, turn not over
    assert t.feed(_final("справи?", speech_final=True)) == "Привіт, як справи?"
    assert t.feed(_final("", speech_final=True)) is None       # nothing pending → no double fire


def test_unfinished_phrase_is_held_for_the_continuation():
    # The live cut: endpointing fired inside a breathing pause — «Я його спробував» went out as a
    # turn and the continuation became a second one. Un-punctuated speech_final must HOLD.
    t = UtteranceTracker()
    assert t.feed(_final("Я його спробував", speech_final=True)) is None   # mid-phrase — held
    assert t.feed(_final("читати рази чотири.", speech_final=True)) == \
        "Я його спробував читати рази чотири."                              # joined into ONE turn


def test_held_unfinished_phrase_flushes_on_utterance_end_backstop():
    t = UtteranceTracker()
    assert t.feed(_final("думаю про Зетрос", speech_final=True)) is None   # held (no punctuation)
    assert t.feed({"type": "UtteranceEnd"}) == "думаю про Зетрос"          # he really was done


def test_utterance_end_is_the_backstop_flush():
    t = UtteranceTracker()
    t.feed(_final("хвіст без крапки"))
    assert t.feed({"type": "UtteranceEnd"}) == "хвіст без крапки"
    assert t.feed({"type": "UtteranceEnd"}) is None            # already flushed
    assert t.feed(_final("", speech_final=True)) is None       # empty finals never accumulate


def test_ws_stats_summary_with_endpoint_stage():
    s = WsStats()
    assert s.summary() == {"turns": 0}
    s.add(endpoint=0.35, llm=0.9, tts=0.3, total=1.3)
    s.add(endpoint=0.45, llm=1.1, tts=0.4, total=1.7)
    s.add(endpoint=0.40, llm=1.0, tts=0.5, total=1.5)
    out = s.summary()
    assert (out["turns"], out["median_s"], out["min_s"], out["max_s"]) == (3, 1.5, 1.3, 1.7)
    assert out["stages_median_s"] == {"endpoint": 0.4, "llm_first": 1.0, "tts_first": 0.4}

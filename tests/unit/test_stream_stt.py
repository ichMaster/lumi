"""v1.6.2 LUMI-200 — voice/stream_stt.py: the Deepgram WS URL, the phrase-hold tracker, the seam.

No network, no audio, no paid calls — the DeepgramStream is exercised through a fake socket via
the injectable ``_connect``; the tracker is pure (the probe's live-tuned semantics, ported here
with the adapter).
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

from voice.stream_stt import (
    DEFAULT_ENDPOINT_MS,
    DeepgramStream,
    UtteranceTracker,
    build_deepgram_url,
)


def test_deepgram_url_streams_linear16_with_endpointing():
    url = urlparse(build_deepgram_url(model="nova-3", lang="uk", endpoint_ms=500))
    q = parse_qs(url.query)
    assert url.scheme == "wss" and url.netloc == "api.deepgram.com"
    assert q["model"] == ["nova-3"] and q["language"] == ["uk"]
    assert q["encoding"] == ["linear16"] and q["sample_rate"] == ["16000"]
    assert q["endpointing"] == ["500"]              # the server VAD ends the turn — no local hangover
    assert q["interim_results"] == ["true"]         # utterance_end + barge-in need interims flowing
    assert q["utterance_end_ms"] == ["1000"]
    assert DEFAULT_ENDPOINT_MS == 500               # 300 cut the owner off mid-phrase live


def _final(text: str, *, speech_final: bool = False) -> dict:
    return {"type": "Results", "is_final": True, "speech_final": speech_final,
            "channel": {"alternatives": [{"transcript": text}]}}


def test_utterance_flushes_on_punctuated_speech_final():
    t = UtteranceTracker()
    assert t.feed({"type": "Results", "is_final": False,
                   "channel": {"alternatives": [{"transcript": "прив"}]}}) is None  # interim — noise
    assert t.feed(_final("Привіт, як")) is None                # a finalized SEGMENT, turn not over
    assert t.feed(_final("справи?", speech_final=True)) == "Привіт, як справи?"
    assert t.feed(_final("", speech_final=True)) is None       # nothing pending → no double fire


def test_unfinished_phrase_is_held_then_joined_or_backstopped():
    t = UtteranceTracker()
    assert t.feed(_final("Я його спробував", speech_final=True)) is None   # mid-phrase — held
    assert t.feed(_final("читати рази чотири.", speech_final=True)) == \
        "Я його спробував читати рази чотири."                              # joined into ONE turn
    assert t.feed(_final("думаю про Зетрос", speech_final=True)) is None   # held again
    assert t.feed({"type": "UtteranceEnd"}) == "думаю про Зетрос"          # he really was done
    assert t.feed({"type": "UtteranceEnd"}) is None                        # already flushed


class _FakeWS:
    """A scripted socket: records sends, yields raw frames, notes close."""

    def __init__(self, frames: list[str]) -> None:
        self._frames = list(frames)
        self.sent: list[bytes] = []
        self.closed = False

    async def send(self, data) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._frames:
            raise StopAsyncIteration
        return self._frames.pop(0)


async def test_stream_seam_round_trip_and_bad_frames_skipped():
    frames = [json.dumps({"type": "Results", "is_final": True, "speech_final": True,
                          "channel": {"alternatives": [{"transcript": "Привіт."}]}}),
              "не-json — пропускається",
              json.dumps({"type": "UtteranceEnd"})]
    fake = _FakeWS(frames)
    seen_headers = {}

    async def connect(url, headers):
        seen_headers.update(headers)
        assert url.startswith("wss://api.deepgram.com")
        return fake

    s = DeepgramStream("sekret", url=build_deepgram_url(model="nova-3"), _connect=connect)
    await s.open()
    await s.send_pcm(b"\x00\x01" * 10)
    events = [e async for e in s.events()]
    await s.close()
    assert seen_headers == {"Authorization": "Token sekret"}
    assert fake.sent == [b"\x00\x01" * 10]                      # binary pcm went to the socket
    assert [e["type"] for e in events] == ["Results", "UtteranceEnd"]  # the bad frame was skipped
    assert fake.closed


def test_config_reads_the_endpoint_window(monkeypatch):
    monkeypatch.setenv("LUMI_VOICE_ENDPOINT_MS", "700")
    from core.config import load_config

    assert load_config().voice_endpoint_ms == 700


def test_probe_imports_the_promoted_tracker():
    # One source of truth — the probe re-exports the /voice adapter, no duplicated tracker.
    from scripts import chain_ws_probe

    assert chain_ws_probe.UtteranceTracker is UtteranceTracker
    assert chain_ws_probe.build_deepgram_url is build_deepgram_url

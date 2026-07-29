"""v1.6.2 LUMI-202 — voice/live.py: the VoiceLoop decisions + the end-to-end spoken turn on fakes.

Fake Deepgram socket + MockStreamTTS + a stubbed turn-runner — no audio hardware, no network, no
paid calls. The TUI methods themselves are Textual glue; the loop logic they drive is pinned here.
"""
from __future__ import annotations

import json

from voice.live import VoiceLoop
from voice.stream_stt import DeepgramStream, build_deepgram_url
from voice.stream_tts import MockStreamTTS, SpeechPipeline


def _interim(text: str) -> dict:
    return {"type": "Results", "is_final": False,
            "channel": {"alternatives": [{"transcript": text}]}}


def _final(text: str, *, speech_final: bool = True) -> dict:
    return {"type": "Results", "is_final": True, "speech_final": speech_final,
            "channel": {"alternatives": [{"transcript": text}]}}


def _loop(on_utterance=None):
    pipeline = SpeechPipeline(MockStreamTTS())
    turns: list[str] = []
    loop = VoiceLoop(stream=None, pipeline=pipeline,
                     on_utterance=on_utterance or turns.append)
    return loop, pipeline, turns


def test_interim_while_she_plays_is_a_barge_in():
    loop, pipeline, _ = _loop()
    pipeline.buffer.feed(b"AUDIO")                      # she is audible
    loop.handle_event(_interim("так але"))
    assert not pipeline.buffer.playing                  # her sound stopped
    loop2, pipeline2, _ = _loop()
    pipeline2.feed_delta("В черзі речення. ")           # not yet audible, but queued
    assert pipeline2.pending == 1
    loop2.handle_event(_interim("зажди"))
    assert pipeline2.pending == 0                       # the queued tail dropped too


def test_interim_in_silence_never_interrupts():
    loop, pipeline, turns = _loop()
    pipeline.feed_delta("Речення. ")
    pipeline.buffer.clear()
    before = pipeline.pending
    loop.handle_event(_interim(""))                     # an empty interim — noise
    loop_events_quiet = pipeline.pending == before
    assert loop_events_quiet and turns == []


def test_repeated_interims_while_streaming_barge_in_only_once():
    # Live bug: Deepgram fires MANY interim events per second while he talks; if the reply is
    # STILL streaming new sentences in during that window, each interim used to re-satisfy
    # "she is audible" and fire ANOTHER interrupt() + [barge-in] note — a rapid churn that showed
    # up as TUI lag (a mounted line per event) and choppy audio (nothing ever finished playing).
    notes: list[str] = []
    pipeline = SpeechPipeline(MockStreamTTS())
    loop = VoiceLoop(stream=None, pipeline=pipeline, on_utterance=lambda t: None,
                     on_note=notes.append)
    pipeline.feed_delta("Перше речення. ")
    loop.handle_event(_interim("а"))                     # the first interim while she's queued/playing
    assert notes == ["[barge-in]"]
    # the reply is STILL generating and queues MORE sentences even though he already interrupted
    pipeline.feed_delta("Друге речення, що йде запізно. ")
    for _ in range(5):                                    # a burst of further interims, same utterance
        loop.handle_event(_interim("а ще"))
    assert notes == ["[barge-in]"]                        # only the FIRST one fired
    assert pipeline.pending == 0                           # the late sentence was muted, never queued
    pipeline.finish_turn()
    pipeline.feed_delta("Наступний хід. ")                 # a fresh turn speaks normally again
    assert pipeline.pending == 1


def test_committed_utterance_runs_exactly_one_turn():
    loop, _, turns = _loop()
    loop.handle_event(_final("Привіт, як справи?"))
    loop.handle_event({"type": "UtteranceEnd"})         # the backstop after a flush — no double fire
    assert turns == ["Привіт, як справи?"]
    assert loop.utterances == turns


async def test_pump_events_end_to_end_spoken_turn():
    # The whole fake wire: Deepgram frames → tracker → ONE turn → the reply streams into TTS.
    frames = [json.dumps(_interim("прив")),
              json.dumps(_final("Привіт, як справи?")),
              json.dumps({"type": "Metadata"})]

    class _FakeWS:
        def __init__(self, items):
            self._items = list(items)
            self.sent = []

        async def send(self, data):
            self.sent.append(data)

        async def close(self):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._items:
                raise StopAsyncIteration
            return self._items.pop(0)

    fake = _FakeWS(frames)

    async def connect(url, headers):
        return fake

    stream = DeepgramStream("k", url=build_deepgram_url(model="nova-3"), _connect=connect)
    await stream.open()
    tts = MockStreamTTS([b"AU"])
    pipeline = SpeechPipeline(tts)
    turns: list[str] = []

    def run_turn(text: str) -> None:  # the stubbed Core turn: stream the reply into the pipeline
        turns.append(text)
        pipeline.feed_delta("Привіт! Все ")
        pipeline.feed_delta("добре. ")
        pipeline.finish_turn()

    loop = VoiceLoop(stream=stream, pipeline=pipeline, on_utterance=run_turn)
    await loop.pump_events()
    spoken = []
    while (s := pipeline.synth_next()) is not None:
        spoken.append(s)
    assert turns == ["Привіт, як справи?"]              # exactly ONE Core turn per utterance
    assert spoken == ["Привіт!", "Все добре."]          # the reply reached her voice, in order
    assert pipeline.buffer.pull(100) == b"AU" * 2


def test_mode_set_config_default_is_text(monkeypatch):
    from core.config import load_config

    monkeypatch.delenv("LUMI_MODE_SET", raising=False)
    assert load_config().mode_set == "text"             # byte-identical default
    monkeypatch.setenv("LUMI_MODE_SET", "voice")
    assert load_config().mode_set == "voice"
    monkeypatch.setenv("LUMI_MODE_SET", "щось-інше")
    assert load_config().mode_set == "text"             # unknown value → the safe fallback

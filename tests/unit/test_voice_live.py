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
    # Barge-in is a QUEUE, not a discard (owner requirement): the sound stops, but nothing queued
    # is thrown away — it plays once resume() is called.
    loop, pipeline, _ = _loop()
    pipeline.buffer.feed(b"AUDIO")                      # she is audible
    loop.handle_event(_interim("так але"))
    assert not pipeline.buffer.playing and pipeline.paused  # her sound stopped, now paused
    loop2, pipeline2, _ = _loop()
    pipeline2.feed_delta("В черзі речення. ")           # not yet audible, but queued
    assert pipeline2.pending == 1
    loop2.handle_event(_interim("зажди"))
    assert pipeline2.pending == 1                       # still queued — nothing was skipped
    assert pipeline2.paused and pipeline2.synth_next() is None  # just not played while paused


def test_interim_in_silence_never_interrupts():
    loop, pipeline, turns = _loop()
    pipeline.feed_delta("Речення. ")
    pipeline.buffer.clear()
    before = pipeline.pending
    loop.handle_event(_interim(""))                     # an empty interim — noise
    loop_events_quiet = pipeline.pending == before
    assert loop_events_quiet and turns == []


def test_repeated_interims_while_streaming_barge_in_only_once():
    # Live bug: Deepgram fires MANY interim events per second while he talks. Barge-in must fire
    # ONCE per burst (not re-interrupt on every interim — that showed up as TUI lag from repeated
    # [barge-in] lines) — but everything the still-streaming reply keeps adding must stay QUEUED,
    # not be discarded, and play once he's done talking.
    notes: list[str] = []
    pipeline = SpeechPipeline(MockStreamTTS())
    loop = VoiceLoop(stream=None, pipeline=pipeline, on_utterance=lambda t: None,
                     on_note=notes.append)
    pipeline.feed_delta("Перше речення. ")
    loop.handle_event(_interim("а"))                     # the first interim while she's queued/playing
    assert notes == ["[barge-in]"]
    # the reply is STILL generating and queues MORE sentences even though he already interrupted
    pipeline.feed_delta("Друге речення, що йде пізніше. ")
    for _ in range(5):                                    # a burst of further interims, same utterance
        loop.handle_event(_interim("а ще"))
    assert notes == ["[barge-in]"]                        # only the FIRST one fired — debounced
    assert pipeline.pending == 2                           # BOTH sentences queued, nothing skipped
    assert pipeline.paused
    loop.handle_event(_final("ну добре."))                 # his utterance commits → she may speak again
    assert not pipeline.paused
    pipeline.feed_delta("Наступний хід. ")                 # its sentences queue right behind the backlog
    assert pipeline.pending == 3


def test_noise_barge_in_with_empty_final_resumes():
    # The live "sound gone forever": a noise interim fires the barge-in, its final comes back
    # EMPTY (nothing to commit) — no utterance ever commits, so the commit-resume never runs.
    # The empty final must lift the pause itself.
    loop, pipeline, turns = _loop()
    pipeline.feed_delta("Речення в черзі. ")
    loop.handle_event(_interim("шум"))                  # noise triggers the barge-in
    assert pipeline.paused
    loop.handle_event(_final("", speech_final=True))    # …and its final is empty — noise confirmed
    assert not pipeline.paused                          # she resumes; nothing was lost
    assert turns == []                                  # and no phantom turn ran
    assert pipeline.pending == 1


def test_utterance_end_with_nothing_pending_resumes():
    # The other recovery path: an UtteranceEnd whose flush found nothing (already flushed / noise).
    loop, pipeline, _ = _loop()
    pipeline.feed_delta("Речення в черзі. ")
    loop.handle_event(_interim("а"))
    assert pipeline.paused
    loop.handle_event({"type": "UtteranceEnd"})         # nothing pending in the tracker
    assert not pipeline.paused


def test_held_partial_keeps_the_pause_until_its_backstop_commits():
    # A held un-punctuated speech_final means he's MID-thought — she must stay quiet; the
    # UtteranceEnd backstop commits the partial (~1 s) and the commit branch resumes.
    loop, pipeline, turns = _loop()
    pipeline.feed_delta("Речення в черзі. ")
    loop.handle_event(_interim("зажди я"))
    assert pipeline.paused
    loop.handle_event(_final("зажди я хочу сказати", speech_final=True))  # held — no punctuation
    assert pipeline.paused                              # still his floor — she keeps waiting
    loop.handle_event({"type": "UtteranceEnd"})         # the backstop commits the partial
    assert not pipeline.paused                          # …and the commit resumed her
    assert turns == ["зажди я хочу сказати"]


def test_stumble_then_continue_both_replies_get_spoken_in_full():
    # Owner's scenario: he starts talking, pauses (a request already fires for the partial
    # utterance), then continues — a SECOND utterance commits and gets its own reply. Both replies
    # must be spoken in full, in order — nothing skipped just because two turns stacked up.
    pipeline = SpeechPipeline(MockStreamTTS())
    loop = VoiceLoop(stream=None, pipeline=pipeline, on_utterance=lambda t: None)

    loop.handle_event(_final("Перше повідомлення."))       # commit 1 → resume() (no-op, nothing queued)
    pipeline.feed_delta("Відповідь перша. ")                # reply 1 starts streaming/playing
    assert pipeline.synth_next() == "Відповідь перша."
    assert pipeline.buffer.playing

    loop.handle_event(_interim("а ще"))                     # he's ALREADY talking again → barge-in
    assert pipeline.paused
    pipeline.feed_delta("Відповідь перша, хвіст. ")         # reply 1 is still streaming — queues, held
    assert pipeline.pending == 1

    loop.handle_event(_final("Друге повідомлення."))        # commit 2 → resume()
    assert not pipeline.paused
    pipeline.finish_turn()                                  # reply 1's turn ends
    pipeline.feed_delta("Відповідь друга. ")                # reply 2 streams in behind it

    assert [pipeline.synth_next() for _ in range(2)] == ["Відповідь перша, хвіст.", "Відповідь друга."]


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


class _FlakyConnector:
    """A scripted Deepgram connector: each call pops the next behavior — a _FakeEventsWS (list of
    raw frames), or an Exception instance to raise (a failed reconnect attempt)."""

    def __init__(self, script):
        self._script = list(script)
        self.connects = 0

    async def __call__(self, url, headers):
        self.connects += 1
        step = self._script.pop(0) if self._script else ConnectionError("no more sockets")
        if isinstance(step, Exception):
            raise step
        return step


class _FakeEventsWS:
    def __init__(self, frames, *, die_after: bool = False):
        self._frames = list(frames)
        self._die_after = die_after

    async def send(self, data):
        pass

    async def close(self):
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._frames:
            if self._die_after:
                raise ConnectionError("socket dropped")
            raise StopAsyncIteration
        return self._frames.pop(0)


async def test_resilient_pump_heals_a_drop_then_degrades_when_retries_exhaust():
    # Outage 1: the socket dies mid-session → attempt 1 fails, attempt 2 reconnects (fresh
    # tracker, "reconnected" note); the healed socket serves one utterance, then closes for good —
    # all 3 attempts fail → the pump returns (the TUI's cue to degrade to text).
    ws1 = _FakeEventsWS([json.dumps(_interim("прив"))], die_after=True)
    ws2 = _FakeEventsWS([json.dumps(_final("Привіт, як справи?"))])
    connector = _FlakyConnector([ws1, ConnectionError("still down"), ws2,
                                 ConnectionError("down"), ConnectionError("down"),
                                 ConnectionError("down")])
    stream = DeepgramStream("k", url=build_deepgram_url(model="nova-3"), _connect=connector)
    await stream.open()

    notes: list[str] = []
    turns: list[str] = []
    pipeline = SpeechPipeline(MockStreamTTS())
    loop = VoiceLoop(stream=stream, pipeline=pipeline, on_utterance=turns.append,
                     on_note=notes.append)
    first_tracker = loop._tracker
    await loop.pump_events_resilient(reconnect=3, backoff_s=0)   # returns ONLY when exhausted
    assert turns == ["Привіт, як справи?"]                       # the healed socket kept working
    assert loop._tracker is not first_tracker                    # fresh tracker per reconnect
    assert notes.count("voice: reconnected") == 1
    assert notes.count("voice: reconnecting 1/3…") == 2          # outage 1 + the final outage
    assert notes[-1] == "voice: reconnecting 3/3…"               # …which exhausted all attempts


async def test_send_pcm_into_a_dead_socket_is_dropped_not_raised():
    class _DeadWS:
        async def send(self, data):
            raise ConnectionError("dead")

        async def close(self):
            pass

    stream = DeepgramStream("k", url="wss://x", _connect=None)
    stream._ws = _DeadWS()
    await stream.send_pcm(b"\x00\x01")                           # swallowed — never a storm
    stream._ws = None
    await stream.send_pcm(b"\x00\x01")                           # a reconnect gap — same


def test_mode_set_config_default_is_text(monkeypatch):
    from core.config import load_config

    monkeypatch.delenv("LUMI_MODE_SET", raising=False)
    assert load_config().mode_set == "text"             # byte-identical default
    monkeypatch.setenv("LUMI_MODE_SET", "voice")
    assert load_config().mode_set == "voice"
    monkeypatch.setenv("LUMI_MODE_SET", "щось-інше")
    assert load_config().mode_set == "text"             # unknown value → the safe fallback

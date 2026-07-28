"""v1.6.2 LUMI-201 — voice/stream_tts.py: the ElevenLabs stream adapter, the speaker pipeline,
barge-in. Mock TTS + scripted deltas — no audio hardware, no network, no paid calls."""
from __future__ import annotations

from voice.stream_tts import (
    ElevenLabsStreamTTS,
    MockStreamTTS,
    SpeakerBuffer,
    SpeechPipeline,
)


# --- the adapter's wire shape ----------------------------------------------------------------------
def test_eleven_stream_request_shape_and_chunks():
    captured = {}

    def opener(url, headers, body):
        captured.update(url=url, headers=headers, body=body)
        yield b"CH1"
        yield b"CH2"

    tts = ElevenLabsStreamTTS("sekret", "voice-123", "eleven_turbo_v2_5", _opener=opener)
    chunks = list(tts.stream("Привіт.", emotion="joy"))
    assert chunks == [b"CH1", b"CH2"]
    assert "voice-123/stream" in captured["url"] and "output_format=pcm_24000" in captured["url"]
    assert captured["headers"]["xi-api-key"] == "sekret"
    import json

    body = json.loads(captured["body"])
    assert body["text"] == "Привіт." and body["model_id"] == "eleven_turbo_v2_5"
    assert 0 <= body["voice_settings"]["stability"] <= 1  # the shared emotion→delivery bias


# --- the speaker buffer ----------------------------------------------------------------------------
def test_speaker_buffer_pulls_exactly_what_plays():
    buf = SpeakerBuffer()
    buf.feed(b"abcdef")
    assert buf.playing
    assert buf.pull(4) == b"abcd"       # consume exactly — nothing dropped (the garbled-voice fix)
    assert buf.pull(4) == b"ef"          # the remainder survives the next pull
    assert not buf.playing
    buf.feed(b"xy")
    buf.clear()
    assert buf.pull(4) == b"" and not buf.playing


# --- the pipeline: deltas → whole clean speakable sentences → the buffer ---------------------------
def test_pipeline_queues_and_speaks_sentences_in_order():
    tts = MockStreamTTS([b"AU"])
    p = SpeechPipeline(tts)
    p.feed_delta("Перше речення. Дру")
    p.feed_delta("ге речення. І хвіст без крапки")
    p.finish_turn()
    spoken = []
    while (s := p.synth_next()) is not None:
        spoken.append(s)
    assert spoken == ["Перше речення.", "Друге речення.", "І хвіст без крапки"]
    assert tts.calls == spoken                       # one synth per sentence, in order
    assert p.buffer.pull(100) == b"AU" * 3           # every chunk reached the speaker buffer


def test_pipeline_guards_think_tags_english_and_trailers():
    tts = MockStreamTTS()
    p = SpeechPipeline(tts)
    p.feed_delta("<think>hidden draft</think>Привіт. ")     # the defensive tag filter
    p.feed_delta("The user is asking about my mood. ")       # an untagged English CoT line
    p.feed_delta("Добре.\nЕМОЦІЯ: calm 0.6")                 # the plain trailer
    p.finish_turn()
    while p.synth_next() is not None:
        pass
    assert tts.calls == ["Привіт.", "Добре."]                # only pure Ukrainian prose was spoken
    assert "The user is asking about my mood." in p.skipped  # the leak is visible, not silent


def test_barge_in_clears_buffer_and_queue_but_only_audio():
    tts = MockStreamTTS([b"AUDIO"])
    p = SpeechPipeline(tts)
    p.feed_delta("Перше. Друге. Третє. ")
    assert p.pending == 3
    assert p.synth_next() == "Перше."                # she starts speaking
    assert p.buffer.playing
    p.interrupt()                                     # he talks over her
    assert not p.buffer.playing                       # the sound stopped…
    assert p.pending == 0                             # …the queued tail is dropped…
    assert p.synth_next() is None
    p.interrupt()                                     # …and a second interrupt is a no-op
    p.feed_delta("Новий хід. ")                       # the NEXT turn speaks normally
    assert p.synth_next() == "Новий хід."


def test_barge_in_mid_sentence_aborts_remaining_chunks():
    p = SpeechPipeline(MockStreamTTS())

    class _InterruptingTTS:
        def __init__(self, pipeline):
            self._p = pipeline

        def stream(self, text, *, emotion=None):
            yield b"FIRST"
            self._p.interrupt()                        # he barges in between chunks
            yield b"AFTER"                             # must never reach the buffer

    p._tts = _InterruptingTTS(p)
    p.feed_delta("Довге речення. ")
    assert p.synth_next() == "Довге речення."          # the sentence returns (turn not cancelled)
    assert not p.buffer.playing                        # FIRST was cleared by the interrupt itself
    assert p.buffer.pull(100) == b""                   # and AFTER was dropped by the epoch check

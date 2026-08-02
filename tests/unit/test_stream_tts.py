"""v1.6.2 LUMI-201 — voice/stream_tts.py: the ElevenLabs stream adapter, the speaker pipeline,
barge-in. Mock TTS + scripted deltas — no audio hardware, no network, no paid calls."""
from __future__ import annotations

from voice.stream_tts import (
    ElevenLabsStreamTTS,
    FirstClauseAssembler,
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


# --- LUMI-204: emotion-colored delivery ------------------------------------------------------------
def test_emotion_supplier_colors_each_sentence_and_hands_over_mid_stream():
    mood = {"now": "calm"}
    tts = MockStreamTTS()
    p = SpeechPipeline(tts, emotion_supplier=lambda: mood["now"])
    p.feed_delta("Перше речення. Друге речення. ")
    assert p.synth_next() == "Перше речення."
    mood["now"] = "playful"                              # the new turn's state validates mid-stream
    assert p.synth_next() == "Друге речення."
    assert tts.emotions == ["calm", "playful"]           # the handover is per-sentence


def test_emotion_supplier_failures_and_absence_degrade_to_neutral():
    tts = MockStreamTTS()
    p = SpeechPipeline(tts)                               # no supplier at all — v1.6.2 behavior
    p.feed_delta("Одне. ")
    p.synth_next()

    def boom():
        raise RuntimeError("state unavailable")

    tts2 = MockStreamTTS()
    p2 = SpeechPipeline(tts2, emotion_supplier=boom)      # a failing supplier degrades, never blocks
    p2.feed_delta("Два. ")
    p2.synth_next()
    assert tts.emotions == [None] and tts2.emotions == [None]


# --- LUMI-203: the first-clause cut ----------------------------------------------------------------
def test_first_chunk_cuts_at_a_clause_boundary_with_enough_words():
    # Streamed like a real reply — small deltas; the cut fires the moment the clause clears the
    # min-word guard, long before the sentence terminator arrives.
    a = FirstClauseAssembler(8)
    assert a.feed("Зараз розкажу ") == []                     # no boundary yet
    assert a.feed("тобі, що я ") == ["Зараз розкажу тобі,"]   # ≥3 words before the comma → cut
    assert a.feed("думаю про це все. І далі буде. ") == \
        ["що я думаю про це все.", "І далі буде."]            # then whole sentences


def test_first_chunk_stays_whole_when_the_sentence_arrives_complete():
    # A sentence that lands in ONE delta has nothing to gain from a cut — spoken whole (prosody).
    a = FirstClauseAssembler(8)
    assert a.feed("Привіт, любий друже, як ти там? ") == ["Привіт, любий друже, як ти там?"]


def test_first_chunk_min_length_guard_holds_tiny_clauses():
    a = FirstClauseAssembler(8)
    assert a.feed("Так, добре, ") == []                       # "Так," (1) and "Так, добре," (2) — held
    assert a.feed("поїхали далі разом. Потім. ") == ["Так, добре, поїхали далі разом.", "Потім."]


def test_first_chunk_word_threshold_never_cuts_mid_word():
    a = FirstClauseAssembler(4)
    assert a.feed("раз два три чотири п'я") == ["раз два три чотири"]  # 4 complete words; "п'я" held
    assert a.feed("ть шість. ") == ["п'ять шість."]           # the partial word survived intact


def test_first_clause_mode_rearms_per_turn_and_zero_is_off():
    tts = MockStreamTTS()
    p = SpeechPipeline(tts, first_clause_words=8)
    p.feed_delta("Привіт, любий друже, ")
    p.feed_delta("як ти ")                                    # streamed — the sentence isn't done yet
    assert p.synth_next() == "Привіт, любий друже,"           # turn 1: the clause cut fired
    p.finish_turn()
    assert p.synth_next() == "як ти"                          # turn 1's tail flushed — nothing lost
    p.feed_delta("Знову, довший початок, ")
    p.feed_delta("і ще ")
    assert p.synth_next() == "Знову, довший початок,"         # re-armed for turn 2
    off = SpeechPipeline(MockStreamTTS(), first_clause_words=0)
    off.feed_delta("Привіт, любий друже, ")
    off.feed_delta("як ти ")
    assert off.synth_next() is None                           # 0 → holds for the full sentence
    off.feed_delta("там сьогодні? ")
    assert off.synth_next() == "Привіт, любий друже, як ти там сьогодні?"


def test_barge_in_pauses_sound_but_keeps_the_queue():
    # The owner's explicit requirement: barge-in is a QUEUE, not a discard — she stays silent while
    # interrupted, then finishes exactly what she hadn't said, in order, once resume() is called.
    tts = MockStreamTTS([b"AUDIO"])
    p = SpeechPipeline(tts)
    p.feed_delta("Перше. Друге. Третє. ")
    assert p.pending == 3
    assert p.synth_next() == "Перше."                # she starts speaking
    assert p.buffer.playing
    p.interrupt()                                     # he talks over her
    assert not p.buffer.playing                       # the sound stopped…
    assert p.pending == 2                             # …but "Друге."/"Третє." are still waiting
    assert p.paused and p.synth_next() is None        # paused — nothing plays while he's talking
    p.interrupt()                                     # …and a second interrupt is a no-op
    p.feed_delta("Ще з того ж ходу. ")                # a still-streaming reply keeps adding —
    assert p.pending == 3                             # …queued too, nothing is skipped
    p.finish_turn()                                    # the interrupted turn ends (filters reset only)
    assert p.paused and p.pending == 3                 # still paused — finish_turn never resumes
    p.resume()                                          # his utterance committed — she may speak again
    assert not p.paused
    p.feed_delta("Новий хід. ")                        # the next turn's sentences queue right after
    assert [p.synth_next() for _ in range(4)] == ["Друге.", "Третє.", "Ще з того ж ходу.", "Новий хід."]


def test_barge_in_mid_sentence_requeues_it_to_replay_whole():
    # «вона договорює свій меседж»: the sentence playing at the moment of the barge-in is NOT
    # lost — it goes back to the FRONT of the queue and replays from its start after the resume.
    p = SpeechPipeline(MockStreamTTS())

    class _InterruptOnceTTS:
        def __init__(self, pipeline):
            self._p = pipeline
            self._fired = False

        def stream(self, text, *, emotion=None):
            yield b"FIRST"
            if not self._fired:
                self._fired = True
                self._p.interrupt()                    # he barges in between chunks — once
                yield b"AFTER"                         # must never reach the buffer
                return
            yield b"REST"                              # the replay after resume streams fully

    p._tts = _InterruptOnceTTS(p)
    p.feed_delta("Довге речення. Наступне. ")
    assert p.synth_next() is None                      # aborted mid-play → requeued, not "spoken"
    assert not p.buffer.playing                        # FIRST was cleared by the interrupt itself
    assert p.pending == 2                              # the interrupted sentence is BACK, in front
    assert p.synth_next() is None                      # paused — nothing plays while he talks
    p.resume()
    assert p.synth_next() == "Довге речення."          # replayed whole…
    assert p.synth_next() == "Наступне."               # …then the rest, in order
    assert p.buffer.pull(100) == (b"FIRST" + b"REST") * 2  # both replays streamed in full


def test_pause_auto_resumes_after_the_watchdog_timeout():
    # The safety valve: if NOTHING ever calls resume() (Deepgram sent no final for the interrupting
    # noise — the live "sound gone forever"), the pause un-sticks itself after auto_resume_s.
    p = SpeechPipeline(MockStreamTTS([b"AU"]), auto_resume_s=4.0)
    p.feed_delta("Речення. ")
    p.interrupt()
    assert p.synth_next() is None and p.paused          # freshly paused — silent
    p._paused_at -= 5.0                                 # pretend 5 s passed with no resume signal
    assert p.synth_next() == "Речення."                 # the watchdog lifted the pause
    assert not p.paused

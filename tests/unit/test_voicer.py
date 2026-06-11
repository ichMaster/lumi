"""The voicer — kind=lili filter, ascending one-at-a-time, retry, first-run skip (v0.14, LUMI-059)."""

from state import fifo
from voice.tts import MockTTS
from voice.voicer import skip_backlog_on_first_run, voice_pending


def test_voices_lili_in_order_and_skips_user(tmp_path):
    outbox, spoken = tmp_path / "outbox.jsonl", tmp_path / "outbox.spoken"
    fifo.append(outbox, "привіт", kind="lili", emotion="joy")
    fifo.append(outbox, "моя репліка", kind="user")     # your mirrored keyboard line
    fifo.append(outbox, "ще", kind="lili", emotion="calm")
    tts, played = MockTTS(), []

    assert voice_pending(outbox, spoken, tts, played.append) == 2   # two lili voiced
    assert tts.calls == [("привіт", "joy"), ("ще", "calm")]          # the user line was NOT synthesized
    assert played == [b"AUDIO:" + "привіт".encode(), b"AUDIO:" + "ще".encode()]  # in order
    assert fifo.load_pointer(spoken) == 3                            # advanced past all 3 (incl. the user)
    assert voice_pending(outbox, spoken, tts, played.append) == 0    # caught up


def test_retry_on_failure_no_advance_no_repeat(tmp_path):
    outbox, spoken = tmp_path / "outbox.jsonl", tmp_path / "outbox.spoken"
    fifo.append(outbox, "a", kind="lili")
    fifo.append(outbox, "b", kind="lili")

    class FlakyTTS:
        def __init__(self): self.fail_on, self.calls = "b", []
        def synth(self, text, *, emotion=None):
            self.calls.append(text)
            if text == self.fail_on:
                raise RuntimeError("boom")
            return b"ok"

    tts = FlakyTTS()
    assert voice_pending(outbox, spoken, tts, lambda a: None) == 1   # only "a"
    assert fifo.load_pointer(spoken) == 1                            # pointer left BEFORE the failed "b"
    tts.fail_on = None                                              # retry: "b" works now
    assert voice_pending(outbox, spoken, tts, lambda a: None) == 1   # "b" voiced, NOT "a" again
    assert fifo.load_pointer(spoken) == 2
    assert tts.calls == ["a", "b", "b"]                             # "a" once; "b" failed then retried


def test_first_run_skips_the_backlog(tmp_path):
    outbox, spoken = tmp_path / "outbox.jsonl", tmp_path / "outbox.spoken"
    for i in range(3):
        fifo.append(outbox, f"old{i}", kind="lili")
    assert skip_backlog_on_first_run(outbox, spoken) == 3           # pointer → the current tail
    assert fifo.load_pointer(spoken) == 3

    tts = MockTTS()
    assert voice_pending(outbox, spoken, tts, lambda a: None) == 0  # the backlog is NOT voiced
    assert tts.calls == []
    fifo.append(outbox, "new", kind="lili")
    assert voice_pending(outbox, spoken, tts, lambda a: None) == 1  # only a NEW reply is voiced
    assert skip_backlog_on_first_run(outbox, spoken) is None        # not a first run anymore


def test_playback_failure_skips_without_re_synthesizing(tmp_path):
    # A stuck speaker must NOT re-synthesize the same reply forever (that burned ElevenLabs credits).
    outbox, spoken = tmp_path / "outbox.jsonl", tmp_path / "outbox.spoken"
    fifo.append(outbox, "a", kind="lili")
    fifo.append(outbox, "b", kind="lili")
    tts = MockTTS()

    def bad_play(_audio):
        raise RuntimeError("no audio player")

    assert voice_pending(outbox, spoken, tts, bad_play) == 0   # nothing heard…
    assert fifo.load_pointer(spoken) == 2                       # …but the pointer advanced past both
    assert [t for t, _ in tts.calls] == ["a", "b"]             # each synthesized ONCE (not repeated)


def test_synth_failure_retries_but_playback_failure_does_not(tmp_path):
    # synth failure leaves the pointer (retry); a later good run re-synths only the un-advanced id.
    outbox, spoken = tmp_path / "outbox.jsonl", tmp_path / "outbox.spoken"
    fifo.append(outbox, "a", kind="lili")

    class DownTTS:
        def synth(self, text, *, emotion=None):
            raise RuntimeError("network down")

    assert voice_pending(outbox, spoken, DownTTS(), lambda a: None) == 0
    assert fifo.load_pointer(spoken) == 0                       # NOT advanced — will retry
    assert voice_pending(outbox, spoken, MockTTS(), lambda a: None) == 1  # recovers, voices "a"


def test_resume_after_restart(tmp_path):
    outbox, spoken = tmp_path / "outbox.jsonl", tmp_path / "outbox.spoken"
    fifo.append(outbox, "a", kind="lili")
    fifo.append(outbox, "b", kind="lili")
    voice_pending(outbox, spoken, MockTTS(), lambda a: None)        # voices a, b → pointer 2
    fifo.append(outbox, "c", kind="lili")
    tts2 = MockTTS()                                                # a "restart" — same spoken pointer
    voice_pending(outbox, spoken, tts2, lambda a: None)
    assert [t for t, _ in tts2.calls] == ["c"]                     # only the new one (resumed)

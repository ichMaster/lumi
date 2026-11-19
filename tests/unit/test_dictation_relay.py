"""v4.7 LUMI-331 — server/dictation.py: the owner's audio in, ``interim`` / ``final`` / ``error`` / ``done`` out.

A fake streaming recognizer (a queue of Deepgram-shaped events) and a fake batch STT — no network, no audio device,
no paid call.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import wave

from server.dictation import BYTES_PER_S, DictationSetup, relay
from voice.stt import MockSTT

PCM = b"\x01\x00" * 160  # 10 ms of 16 kHz mono int16
STOP = json.dumps({"type": "stop"})
CANCEL = json.dumps({"type": "cancel"})


def interim(text):
    return {"type": "Results", "is_final": False, "channel": {"alternatives": [{"transcript": text}]}}


def final(text, *, speech_final=False):
    return {"type": "Results", "is_final": True, "speech_final": speech_final,
            "channel": {"alternatives": [{"transcript": text}]}}


class FakeStream:
    """Deepgram's side: events the test pushes; ``finish`` (CloseStream) sends the tail and ends the stream."""

    def __init__(self, tail=()):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.pcm: list[bytes] = []
        self.tail = list(tail)
        self.finished = False
        self.closed = False

    async def send_pcm(self, data):
        self.pcm.append(data)

    async def finish(self):
        self.finished = True
        for event in self.tail:
            self.queue.put_nowait(event)
        self.queue.put_nowait(None)

    async def close(self):
        self.closed = True

    async def events(self):
        while True:
            event = await self.queue.get()
            if event is None:
                return
            if isinstance(event, Exception):
                raise event
            yield event


def client(script, stream=None):
    """The window's side: audio frames, control messages, ("events", [...]) pushed to the recognizer meanwhile,
    "hang" (nothing more comes), None (the client leaves)."""
    items = list(script)

    async def receive():
        while items:
            item = items.pop(0)
            if isinstance(item, tuple) and item[0] == "events":
                for event in item[1]:
                    stream.queue.put_nowait(event)
                for _ in range(10):
                    await asyncio.sleep(0)
                continue
            if item == "hang":
                break
            return item
        await asyncio.sleep(30)
        return None

    return receive


async def run(script, *, stream=None, stt=None, fail_open=False, **kw):
    sent = []

    async def send(message):
        sent.append(message)

    async def factory():
        if fail_open:
            raise ConnectionError("down")
        return stream

    setup = DictationSetup(stt=stt or MockSTT(""), stream_factory=factory if (stream or fail_open) else None,
                           lang="uk")
    outcome = await relay(client(script, stream), send, setup, **kw)
    return outcome, sent


def shapes(sent):
    return [(m["type"], m.get("text", m.get("mode", m.get("detail")))) for m in sent]


# --- stream mode ------------------------------------------------------------------------------------------------

async def test_stream_mode_shows_the_phrase_live_and_settles_it_with_the_hold_kept():
    stream = FakeStream()
    outcome, sent = await run([
        PCM,
        ("events", [interim("Я його")]),
        ("events", [final("Я його спробував", speech_final=True)]),  # a pause mid-phrase: held, still shown
        ("events", [interim("читати")]),
        ("events", [final("читати рази чотири.", speech_final=True)]),
        ("events", [final("і ще", speech_final=True)]),
        STOP,
    ], stream=stream)
    assert outcome == "done"
    assert shapes(sent) == [
        ("ready", "stream"),
        ("interim", "Я його"),
        ("interim", "Я його спробував"),
        ("interim", "Я його спробував читати"),
        ("final", "Я його спробував читати рази чотири."),
        ("interim", "і ще"),
        ("final", "і ще"),  # the phrase still held at the stop is flushed, not lost
        ("done", None),
    ]
    assert stream.pcm == [PCM] and stream.finished and stream.closed


async def test_the_last_results_after_the_stop_still_count():
    stream = FakeStream(tail=[final("кінець.", speech_final=True)])
    outcome, sent = await run([PCM, STOP], stream=stream)
    assert outcome == "done"
    assert shapes(sent)[-2:] == [("final", "кінець."), ("done", None)]


async def test_a_recognizer_that_never_ends_is_cut_after_the_stop():
    class Silent(FakeStream):
        async def finish(self):
            self.finished = True  # CloseStream sent, but no last results and no close

    stream = Silent()
    outcome, sent = await run([PCM, ("events", [final("так", speech_final=True)]), STOP], stream=stream,
                              finish_s=0.05)
    assert outcome == "done"
    assert shapes(sent)[-2:] == [("final", "так"), ("done", None)]


async def test_cancel_sends_nothing_more_and_closes_the_stream():
    stream = FakeStream()
    outcome, sent = await run([PCM, ("events", [interim("а")]), CANCEL], stream=stream)
    assert outcome == "cancel"
    assert shapes(sent) == [("ready", "stream"), ("interim", "а")]
    assert stream.closed and not stream.finished


async def test_a_stream_that_breaks_mid_recording_ends_with_one_readable_error():
    stream = FakeStream()
    outcome, sent = await run([PCM, ("events", [RuntimeError("секретні слова")]), "hang"], stream=stream)
    assert outcome == "error"
    assert shapes(sent) == [("ready", "stream"), ("error", "the recognizer stopped (RuntimeError)")]
    assert "секретні" not in json.dumps(sent, ensure_ascii=False)


# --- batch mode -------------------------------------------------------------------------------------------------

class RecordingSTT:
    def __init__(self, text="Привіт, світ.", error=None):
        self.text = text
        self.error = error
        self.calls = []

    def recognize(self, audio, *, lang="uk", content_type="audio/wav"):
        self.calls.append((audio, lang))
        if self.error is not None:
            raise self.error
        return self.text


async def test_batch_mode_recognizes_the_whole_recording_once_on_the_stop():
    stt = RecordingSTT()
    outcome, sent = await run([PCM, PCM, STOP], stt=stt)
    assert outcome == "done"
    assert shapes(sent) == [("ready", "batch"), ("final", "Привіт, світ."), ("done", None)]
    assert len(stt.calls) == 1
    audio, lang = stt.calls[0]
    with wave.open(io.BytesIO(audio)) as w:  # a WAV of exactly what was said
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16_000)
        assert w.readframes(w.getnframes()) == PCM + PCM
    assert lang == "uk"


async def test_an_empty_recognition_is_done_without_a_final():
    outcome, sent = await run([PCM, STOP], stt=MockSTT(""))
    assert outcome == "done" and shapes(sent) == [("ready", "batch"), ("done", None)]


async def test_a_stream_that_cant_open_falls_back_to_batch():
    stt = RecordingSTT("Так.")
    outcome, sent = await run([PCM, STOP], stt=stt, fail_open=True)
    assert outcome == "done"
    assert shapes(sent) == [("ready", "batch"), ("final", "Так."), ("done", None)]


async def test_a_batch_failure_is_one_error_with_the_type_only():
    stt = RecordingSTT(error=ValueError("слова з аудіо"))
    outcome, sent = await run([PCM, STOP], stt=stt)
    assert outcome == "error"
    assert shapes(sent) == [("ready", "batch"), ("error", "recognition failed (ValueError)")]


# --- the caps, the client leaving, nothing kept -----------------------------------------------------------------

async def test_five_minutes_of_audio_is_a_stop():
    stt = RecordingSTT("Довго.")
    max_s = 32 / BYTES_PER_S  # the cap in bytes: 32
    outcome, sent = await run([b"\x01" * 64, "hang"], stt=stt, max_s=max_s)
    assert outcome == "done" and shapes(sent)[-1] == ("done", None)
    with wave.open(io.BytesIO(stt.calls[0][0])) as w:
        assert w.getnframes() * 2 == 32  # nothing past the cap is kept


async def test_five_minutes_by_the_clock_is_a_stop():
    ticks = iter([0.0, 301.0])
    outcome, sent = await run([PCM, "hang"], stt=RecordingSTT("Так."), clock=lambda: next(ticks))
    assert outcome == "done" and shapes(sent)[-1] == ("done", None)


async def test_no_audio_for_the_idle_window_is_an_error():
    outcome, sent = await run(["hang"], idle_s=0.05)
    assert outcome == "error"
    assert sent[-1]["type"] == "error" and sent[-1]["detail"].startswith("no audio for")


async def test_a_client_that_leaves_ends_it_quietly():
    stream = FakeStream()
    outcome, sent = await run([PCM, None], stream=stream)
    assert outcome == "gone" and shapes(sent) == [("ready", "stream")] and stream.closed


async def test_a_send_into_a_closed_socket_ends_it_as_gone():
    async def send(_message):
        raise RuntimeError("closed")

    outcome = await relay(client([PCM, STOP]), send, DictationSetup(stt=MockSTT("x")))
    assert outcome == "gone"


async def test_nothing_is_written_and_the_log_holds_no_words(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    with caplog.at_level(logging.INFO, logger="lumi.dictation"):
        outcome, _ = await run([PCM, STOP], stt=RecordingSTT("Таємний текст."))
    assert outcome == "done"
    assert list(tmp_path.iterdir()) == []
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("dictation:")]
    assert line and "batch" in line[0] and "done" in line[0] and "Таємний" not in caplog.text

"""v4.7 LUMI-333 — desktop/dictation.py: the shell's side of dictation, with a fake mic and a fake socket.

No device, no network, no paid call: the mic is a callback the test drives, the server a scripted socket.
"""

from __future__ import annotations

import json
import queue
import threading

import pytest

from desktop.dictation import Busy, Dictation, level_of, ws_url
from desktop.proxy import Upstream

BLOCK = (1000).to_bytes(2, "little", signed=True) * 1600  # 100 ms at a steady level
UP = Upstream(url="http://127.0.0.1:9", token="T0KEN")


class FakeMic:
    def __init__(self):
        self.opened = threading.Event()
        self.closed = threading.Event()
        self.device = None
        self.on_block = None

    def __call__(self, device, on_block):  # the open_mic seam
        self.device, self.on_block = device, on_block
        self.opened.set()
        return self

    def close(self):
        self.closed.set()

    def feed(self, block=BLOCK, n=1):
        for _ in range(n):
            self.on_block(block)


class FakeSocket:
    """The server: ``ready`` when the test says so; ``stop`` → the scripted end (``final`` + ``done`` by default)."""

    def __init__(self, *, ready=True, on_stop=None):
        self.inbox: queue.Queue = queue.Queue()
        self.sent: list = []
        self.closed = threading.Event()
        self.url = self.token = None
        self.on_stop = [{"type": "final", "text": "Привіт, світ."}, {"type": "done"}] if on_stop is None else on_stop
        if ready:
            self.say({"type": "ready", "mode": "stream"})

    def connect(self, url, token):  # the connect seam
        self.url, self.token = url, token
        return self

    def say(self, message):
        self.inbox.put(json.dumps(message))

    def send(self, data):
        if self.closed.is_set():
            raise ConnectionError("closed")
        self.sent.append(data)
        if data == json.dumps({"type": "stop"}):
            for message in self.on_stop:
                self.say(message)

    def recv(self, timeout=None):
        if self.closed.is_set():
            raise ConnectionError("closed")
        try:
            return self.inbox.get(timeout=timeout)
        except queue.Empty:
            if self.closed.is_set():
                raise ConnectionError("closed") from None
            raise TimeoutError from None

    def close(self):
        self.closed.set()

    @property
    def audio(self):
        return [d for d in self.sent if isinstance(d, bytes)]

    @property
    def controls(self):
        return [json.loads(d)["type"] for d in self.sent if isinstance(d, str)]


def start(dictation):
    events: list = []
    dictation.claim()
    out: dict = {}
    thread = threading.Thread(target=lambda: out.setdefault("outcome", dictation.run(events.append)), daemon=True)
    thread.start()
    return events, thread, out


def kinds(events):
    return [e["type"] for e in events]


def test_a_dictation_streams_the_mic_and_passes_the_words_on():
    mic, sock = FakeMic(), FakeSocket()
    d = Dictation(UP, device="MacBook", open_mic=mic, connect=sock.connect)
    events, thread, out = start(d)
    assert mic.opened.wait(5)
    mic.feed(n=3)
    sock.say({"type": "interim", "text": "Привіт"})
    assert d.stop()
    thread.join(5)
    assert out["outcome"] == "done"
    assert sock.url == "ws://127.0.0.1:9/v1/dictation" and sock.token == "T0KEN" and mic.device == "MacBook"
    assert sock.audio == [BLOCK] * 3 and sock.controls == ["stop"]  # every block, then the stop
    assert kinds(events)[0] == "ready" and kinds(events)[-2:] == ["final", "done"]
    assert "interim" in kinds(events) and "level" in kinds(events)
    assert mic.closed.is_set() and sock.closed.is_set() and not d.running


def test_the_mic_opens_only_after_the_server_is_ready():
    mic, sock = FakeMic(), FakeSocket(ready=False)
    d = Dictation(UP, open_mic=mic, connect=sock.connect)
    events, thread, _ = start(d)
    assert not mic.opened.wait(0.3)  # no ready yet → no mic
    sock.say({"type": "ready", "mode": "batch"})
    assert mic.opened.wait(5)
    d.stop()
    thread.join(5)
    assert kinds(events)[0] == "ready"


def test_cancel_closes_the_mic_and_the_socket_and_passes_nothing_more_on():
    mic, sock = FakeMic(), FakeSocket()
    d = Dictation(UP, open_mic=mic, connect=sock.connect)
    events, thread, out = start(d)
    assert mic.opened.wait(5)
    mic.feed()
    seen = len(events)
    assert d.cancel()
    sock.say({"type": "final", "text": "пізно"})  # whatever the server still says never reaches the page
    thread.join(5)
    assert out["outcome"] == "cancel"
    assert mic.closed.is_set() and sock.closed.is_set()
    assert "cancel" in sock.controls and "stop" not in sock.controls
    assert len(events) == seen and "final" not in kinds(events)


def test_a_server_error_ends_it_with_the_mic_closed():
    mic, sock = FakeMic(), FakeSocket()
    d = Dictation(UP, open_mic=mic, connect=sock.connect)
    events, thread, out = start(d)
    assert mic.opened.wait(5)
    sock.say({"type": "error", "detail": "the recognizer stopped (RuntimeError)"})
    thread.join(5)
    assert out["outcome"] == "error" and events[-1] == {"type": "error",
                                                         "detail": "the recognizer stopped (RuntimeError)"}
    assert mic.closed.is_set() and sock.closed.is_set() and not d.running


def test_the_five_minute_cap_is_a_stop():
    mic, sock = FakeMic(), FakeSocket()
    d = Dictation(UP, open_mic=mic, connect=sock.connect, max_s=0.25)  # 8,000 bytes: two and a half blocks
    events, thread, out = start(d)
    assert mic.opened.wait(5)
    mic.feed(n=5)
    thread.join(5)
    assert out["outcome"] == "done" and sock.controls == ["stop"]
    assert sum(len(b) for b in sock.audio) == 8000  # nothing past the cap reaches the server
    assert mic.closed.is_set()


def test_closing_the_window_cancels_a_running_dictation():
    mic, sock = FakeMic(), FakeSocket()
    d = Dictation(UP, open_mic=mic, connect=sock.connect)
    _, thread, out = start(d)
    assert mic.opened.wait(5)
    d.close()
    thread.join(5)
    assert out["outcome"] == "cancel" and mic.closed.is_set() and sock.closed.is_set()


def test_one_dictation_at_a_time():
    d = Dictation(UP, open_mic=FakeMic(), connect=FakeSocket().connect)
    d.claim()
    with pytest.raises(Busy):
        d.claim()


def test_an_unreachable_server_is_one_error_and_the_mic_never_opens():
    mic = FakeMic()

    def refuse(url, token):
        raise ConnectionRefusedError("no")

    d = Dictation(UP, open_mic=mic, connect=refuse)
    events, thread, out = start(d)
    thread.join(5)
    assert out["outcome"] == "error" and events == [{"type": "error",
                                                     "detail": "the server can't be reached (ConnectionRefusedError)"}]
    assert not mic.opened.is_set() and not d.running


def test_a_mic_that_cant_open_is_one_error_and_the_server_is_told():
    sock = FakeSocket()

    def no_mic(device, on_block):
        raise OSError("no permission")

    d = Dictation(UP, open_mic=no_mic, connect=sock.connect)
    events, thread, out = start(d)
    thread.join(5)
    assert out["outcome"] == "error" and events[-1]["detail"] == "the microphone can't be opened (OSError)"
    assert sock.controls == ["cancel"] and sock.closed.is_set()


def test_the_level_is_paced_and_measures_the_peak():
    now = [0.0]
    mic, sock = FakeMic(), FakeSocket()
    d = Dictation(UP, open_mic=mic, connect=sock.connect, clock=lambda: now[0])
    events, thread, _ = start(d)
    assert mic.opened.wait(5)
    mic.feed(n=5)  # the clock stands still: one level for the five blocks
    for _ in range(50):
        if len(sock.audio) == 5:
            break
        threading.Event().wait(0.01)
    now[0] = 1.0
    mic.feed()
    d.stop()
    thread.join(5)
    levels = [e["value"] for e in events if e["type"] == "level"]
    assert len(levels) == 2 and levels[0] == pytest.approx(1000 / 32768, abs=0.001)
    assert level_of(b"") == 0.0 and level_of((-32768).to_bytes(2, "little", signed=True)) == 1.0


def test_a_page_that_goes_away_cancels_the_dictation():
    mic, sock = FakeMic(), FakeSocket()
    d = Dictation(UP, open_mic=mic, connect=sock.connect)
    d.claim()

    def emit(message):
        if message["type"] == "interim":
            raise BrokenPipeError("the page reloaded")

    out: dict = {}
    thread = threading.Thread(target=lambda: out.setdefault("outcome", d.run(emit)), daemon=True)
    thread.start()
    assert mic.opened.wait(5)
    sock.say({"type": "interim", "text": "а"})
    thread.join(5)
    assert out["outcome"] == "cancel" and mic.closed.is_set() and sock.closed.is_set()


def test_availability_names_what_is_missing():
    assert Dictation(Upstream(url="", token=""), open_mic=FakeMic(), connect=FakeSocket().connect).available() == (
        False, "no server is set")
    assert Dictation(UP, open_mic=FakeMic(), connect=FakeSocket().connect).available() == (True, "")


def test_the_socket_address_follows_the_server():
    assert ws_url("http://192.168.1.197:8765") == "ws://192.168.1.197:8765/v1/dictation"
    assert ws_url("https://lumi.example/") == "wss://lumi.example/v1/dictation"

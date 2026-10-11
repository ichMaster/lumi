"""RemoteCore hears the server's pushes (LUMI-221) — over a scripted streaming transport: the snapshot
follows ``state``, its own thoughts are skipped, a drop reconnects with backoff and says so once, an older
server (404) ends it quietly. No sockets, no paid API."""

import threading
import time

import httpx
import pytest

import tui.remote as remote
from tui.remote import CLIENT_HEADER, RemoteCore


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch):
    monkeypatch.setattr(remote, "LISTEN_BACKOFF_S", (0.01, 0.04))


def _sse(event: str, data: str) -> bytes:
    return f"event: {event}\ndata: {data}\n\n".encode()


class _Script(httpx.SyncByteStream):
    def __init__(self, chunks, end):
        self.chunks, self.end = chunks, end

    def __iter__(self):
        yield from self.chunks
        if self.end == "drop":
            raise httpx.ReadError("connection dropped")
        if self.end == "hang":  # an open stream — until the test stops the listener
            while True:
                time.sleep(0.01)
                yield b": heartbeat\n\n"


class _Server:
    """``/v1/events`` answers connection by connection from a script; everything else is a plain 200."""

    def __init__(self, connections):
        self.connections = list(connections)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path != "/v1/events":
            return httpx.Response(200, json={"state": {}})
        self.requests.append(request)
        nxt = self.connections.pop(0) if self.connections else ("refuse",)
        if nxt[0] == "refuse":
            raise httpx.ConnectError("refused")
        if nxt[0] == "status":
            return httpx.Response(nxt[1])
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=_Script(nxt[1], nxt[2]))


def _remote(server):
    transport = httpx.MockTransport(server)
    return RemoteCore("http://lumi", "tok", client=httpx.Client(transport=transport, base_url="http://lumi"),
                      events_client=httpx.Client(transport=transport, base_url="http://lumi"))


def _until(cond, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.005)


def test_state_updates_the_snapshot_and_its_own_thoughts_are_skipped():
    server = _Server([])
    rc = _remote(server)
    server.connections.append(("stream", [
        _sse("state", '{"origin": null, "state": {"style": "warm", "mood": "тихо"}}'),
        _sse("thought", '{"kind": "think", "text": "чужа", "origin": "cli-1"}'),
        _sse("thought", f'{{"kind": "think", "text": "своя", "origin": "{rc.client_id}"}}'),
        _sse("turn", '{"emotion": "joy", "intensity": 0.8, "theme": null, "origin": "cli-1"}'),
    ], "hang"))
    heard: list = []
    assert rc.listen(lambda e, d: heard.append((e, d))) is True
    _until(lambda: len(heard) == 3)
    rc.stop()
    assert [e for e, _ in heard] == ["state", "thought", "turn"]
    assert heard[1][1]["text"] == "чужа"  # its own thought never comes back through the pushes
    assert rc.style == "warm" and rc.mood == "тихо"  # the snapshot followed the push alone
    req = server.requests[0]
    assert req.headers[CLIENT_HEADER] == rc.client_id and req.headers["Authorization"] == "Bearer tok"


def test_a_drop_reconnects_says_so_once_and_resyncs():
    server = _Server([
        ("stream", [_sse("state", '{"origin": null, "state": {"style": "a"}}')], "drop"),
        ("refuse",), ("refuse",),  # the server is down for a while
        ("stream", [_sse("state", '{"origin": null, "state": {"style": "b"}}')], "hang"),
    ])
    rc = _remote(server)
    heard: list = []
    rc.listen(lambda e, d: heard.append(e))
    _until(lambda: heard[-1:] == ["state"] and "_online" in heard)
    rc.stop()
    assert heard == ["state", "_offline", "_online", "state"]  # one note per outage, not per attempt
    assert rc.style == "b" and len(server.requests) == 4


def test_an_older_server_without_the_route_ends_quietly():
    server = _Server([("status", 404)])
    rc = _remote(server)
    heard: list = []
    rc.listen(lambda e, d: heard.append(e))
    _until(lambda: not rc._listener.is_alive())
    assert heard == [] and len(server.requests) == 1  # no retries, no outage note


def test_a_request_that_reaches_the_server_cuts_the_backoff_short(monkeypatch):
    monkeypatch.setattr(remote, "LISTEN_BACKOFF_S", (30.0, 30.0))  # a long wait…
    server = _Server([("refuse",), ("stream", [_sse("state", '{"origin": null, "state": {}}')], "hang")])
    rc = _remote(server)
    heard: list = []
    rc.listen(lambda e, d: heard.append(e))
    _until(lambda: heard == ["_offline"])
    rc.refresh()  # …cut short: the server answers a request again
    _until(lambda: heard == ["_offline", "_online", "state"], timeout=2)
    rc.stop()


def test_a_failing_handler_never_kills_the_listener():
    server = _Server([("stream", [_sse("state", '{"origin": null, "state": {}}'),
                                  _sse("turn", '{"emotion": "calm", "intensity": 0.5, "theme": null}')], "hang")])
    rc = _remote(server)
    heard: list = []

    def handler(event, data):
        heard.append(event)
        if event == "state":
            raise RuntimeError("the UI is gone")

    rc.listen(handler)
    _until(lambda: heard == ["state", "turn"])
    rc.stop()


def test_no_events_transport_means_no_listener():
    rc = RemoteCore("http://lumi", "tok", client=httpx.Client(transport=httpx.MockTransport(_Server([])),
                                                              base_url="http://lumi"))
    assert rc.listen(lambda e, d: None) is False  # an injected test client can't stream — no pushes


def test_stop_never_blocks_and_ends_the_thread():
    server = _Server([("stream", [_sse("state", '{"origin": null, "state": {}}')], "hang")])
    rc = _remote(server)
    got = threading.Event()
    rc.listen(lambda e, d: got.set())
    assert got.wait(2)
    started = time.monotonic()
    rc.stop()
    assert time.monotonic() - started < 0.5
    rc._listener.join(2)
    assert not rc._listener.is_alive()


def test_every_request_names_this_client():
    seen: list = []

    def handler(request):
        seen.append(request.headers.get(CLIENT_HEADER))
        return httpx.Response(200, json={"state": {"session_id": "s"}})

    rc = RemoteCore("http://lumi", "tok", client=httpx.Client(transport=httpx.MockTransport(handler),
                                                              base_url="http://lumi"))
    rc.refresh()
    rc.command("/mood")
    assert seen == [rc.client_id, rc.client_id] and len(rc.client_id) == 12


# --- v2.5: SSE ids, resume, own conversation lines ------------------------------------------------------------

def test_frames_carry_their_ids():
    from tui.remote import parse_sse, parse_sse_frames

    raw = ["event: message", "id: b00t-7", 'data: {"text": "так"}', "", "event: state", 'data: {"state": {}}', ""]
    assert list(parse_sse_frames(raw)) == [("message", {"text": "так"}, "b00t-7"), ("state", {"state": {}}, None)]
    assert list(parse_sse(raw)) == [("message", {"text": "так"}), ("state", {"state": {}})]  # the v2.3 pairs


def test_a_reconnect_resumes_after_the_last_id_it_saw():
    server = _Server([
        ("stream", [b'event: message\nid: b00t-3\ndata: {"role": "lili", "text": "1", "origin": null}\n\n'],
         "drop"),
        ("stream", [_sse("state", '{"origin": null, "state": {}}')], "hang"),
    ])
    rc = _remote(server)
    heard: list = []
    rc.listen(lambda e, d: heard.append(e))
    _until(lambda: heard[-1:] == ["state"])
    rc.stop()
    assert "Last-Event-ID" not in server.requests[0].headers  # a fresh listener asks for the whole replay
    assert server.requests[1].headers["Last-Event-ID"] == "b00t-3"  # the resume — nothing shown twice


def test_its_own_conversation_lines_are_skipped():
    server = _Server([])
    rc = _remote(server)
    server.connections.append(("stream", [
        _sse("message", f'{{"role": "user", "text": "моє", "source": "client", "origin": "{rc.client_id}"}}'),
        _sse("message", '{"role": "user", "text": "з телеграму", "source": "telegram", "origin": null}'),
    ], "hang"))
    heard: list = []
    rc.listen(lambda e, d: heard.append(d.get("text")))
    _until(lambda: len(heard) == 1)
    rc.stop()
    assert heard == ["з телеграму"]

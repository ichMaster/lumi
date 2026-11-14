"""v2.12.1 LUMI-266 — the desktop window's local proxy against a fake server (``tests/_desktop_world.py``): the
page served with the launch key, the guard (the key, the Host), the forwarding with the token, a streamed body
relayed frame by frame, and the errors the page shows. Loopback only — no real server, no network."""

from __future__ import annotations

import http.client
import json
import socket

import pytest

from desktop.proxy import KEY_HEADER, KEY_META, Upstream
from tests._desktop_world import KEY, TOKEN, FakeServer, request, start_proxy


@pytest.fixture
def world():
    server = FakeServer()
    proxy = start_proxy(Upstream(server.url, TOKEN))
    yield server, proxy
    server.release.set()
    proxy.stop()
    server.close()


def test_the_page_is_served_with_the_launch_key_and_nothing_outside_it(world):
    _, proxy = world
    status, headers, body = request(proxy, "GET", "/", key=False)
    assert status == 200 and headers["content-type"].startswith("text/html")
    assert f'<meta name="{KEY_META}" content="{KEY}">' in body.decode()
    for path in ("/../../pyproject.toml", "/%2e%2e/%2e%2e/pyproject.toml", "/nope.js", "/..%2fproxy.py"):
        assert request(proxy, "GET", path, key=False)[0] == 404, path
    assert request(proxy, "POST", "/", key=False, body={})[0] == 405


def test_the_api_needs_the_key_and_the_proxys_own_host(world):
    server, proxy = world
    assert request(proxy, "GET", "/v1/state", key=False)[0] == 403
    assert request(proxy, "GET", "/v1/state", headers={KEY_HEADER: "a-guess"})[0] == 403
    assert request(proxy, "GET", "/v1/state", headers={"Host": f"evil.example:{proxy.port}"})[0] == 403
    assert server.requests == []  # nothing reached the server


def test_a_request_is_forwarded_with_the_token_and_the_page_headers(world):
    server, proxy = world
    status, headers, body = request(proxy, "GET", "/v1/state",
                                    headers={"X-Lumi-Client": "page-1", "Last-Event-ID": "7",
                                             "Authorization": "Bearer from-the-page", "Cookie": "x=1"})
    assert status == 200 and json.loads(body) == {"env": "dev", "version": "2.12.1"}
    [seen] = server.requests
    assert seen["headers"]["Authorization"] == f"Bearer {TOKEN}"  # the proxy's, never the page's
    assert seen["headers"]["X-Lumi-Client"] == "page-1" and seen["headers"]["Last-Event-ID"] == "7"
    assert "Cookie" not in seen["headers"] and KEY_HEADER not in seen["headers"]
    assert TOKEN not in body.decode() and all(TOKEN not in v for v in headers.values())
    status, _, body = request(proxy, "POST", "/v1/command", body={"line": "/mood", "confirmed": False})
    assert status == 200 and json.loads(body)["echo"] == {"line": "/mood", "confirmed": False}
    assert server.requests[-1]["headers"]["Content-Type"] == "application/json"


def test_only_the_pages_routes_are_forwarded(world):
    server, proxy = world
    for method, path in (("GET", "/v1/admin"), ("POST", "/v1/state"), ("GET", "/v1/turn"), ("GET", "/v1")):
        assert request(proxy, method, path, body={} if method == "POST" else None)[0] == 404, path
    assert server.requests == []


def test_a_stream_is_relayed_frame_by_frame(world):
    server, proxy = world
    conn = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=10)
    conn.request("GET", "/v1/events", headers={KEY_HEADER: KEY})
    res = conn.getresponse()
    assert res.status == 200 and res.getheader("Content-Type").startswith("text/event-stream")
    first = b""
    while not first.endswith(b"\n\n"):
        first += res.read1(1024) or b""
    assert b"event: state" in first and not server.release.is_set()  # it arrived while the server still waits
    server.release.set()
    rest = res.read()
    conn.close()
    assert b"event: message" in rest and "привіт".encode() in rest
    status, _, body = request(proxy, "POST", "/v1/turn/stream", body={"text": "hi", "turn_id": "t1"})
    assert status == 200 and body.decode().count("event: ") == 2


def test_the_errors_the_page_shows(world):
    server, _ = world
    wrong = start_proxy(Upstream(server.url, "a-wrong-token"))
    status, headers, body = request(wrong, "GET", "/v1/state")
    assert status == 401 and json.loads(body)["kind"] == "token_refused"
    assert "a-wrong-token" not in body.decode()
    wrong.stop()

    with socket.socket() as s:  # a port nothing listens on
        s.bind(("127.0.0.1", 0))
        dead_port = s.getsockname()[1]
    down = start_proxy(Upstream(f"http://127.0.0.1:{dead_port}", TOKEN))
    status, _, body = request(down, "GET", "/v1/state")
    assert status == 502 and json.loads(body)["kind"] == "unreachable"
    down.stop()

    unset = start_proxy(Upstream(None, ""))
    status, _, body = request(unset, "GET", "/v1/state")
    assert status == 503 and json.loads(body)["kind"] == "no_server"
    assert request(unset, "GET", "/", key=False)[0] == 200  # the page itself still loads, to say so
    unset.stop()


def test_no_answer_carries_a_cors_header(world):
    _, proxy = world
    answers = [request(proxy, "GET", "/", key=False), request(proxy, "GET", "/v1/state"),
               request(proxy, "GET", "/v1/state", key=False), request(proxy, "OPTIONS", "/v1/state")]
    assert answers[-1][0] == 405
    assert all(not any(h.startswith("access-control-") for h in headers) for _, headers, _ in answers)

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


def test_the_whole_page_is_served_from_desktop_ui(world):
    _, proxy = world
    kinds = {"/": "text/html", "/app.js": "javascript", "/style.css": "text/css", "/tokens.css": "text/css",
             "/strings.en.json": "application/json"}
    for path, kind in kinds.items():
        status, headers, body = request(proxy, "GET", path, key=False)
        assert status == 200 and kind in headers["content-type"] and body, path
    html = request(proxy, "GET", "/", key=False)[2].decode()
    for ref in ("tokens.css", "style.css", "app.js"):
        assert f'"{ref}"' in html


def test_review_1_a_stalled_stream_ends_at_the_read_timeout():
    import time

    from desktop.proxy import Proxy

    server = FakeServer()  # its events stream sends one frame, then waits (up to 5 s) for a release that never comes
    proxy = Proxy(Upstream(server.url, TOKEN), key=KEY, events_read_s=0.5)
    proxy.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=10)
        conn.request("GET", "/v1/events", headers={KEY_HEADER: KEY})
        res = conn.getresponse()
        started = time.monotonic()
        data = res.read()  # ends when the proxy gives up on the silent server, not when the server ends
        elapsed = time.monotonic() - started
        conn.close()
        assert b"event: state" in data and b"event: message" not in data
        assert elapsed < 3.0
        assert proxy.timeout_for("/v1/events").read == 0.5 and proxy.timeout_for("/v1/turn").read == 600.0
    finally:
        server.release.set()
        proxy.stop()
        server.close()


def test_review_3_no_page_may_frame_the_chat(world):
    _, proxy = world
    for path in ("/", "/app.js", "/style.css"):
        _, headers, _ = request(proxy, "GET", path, key=False)
        assert headers["x-frame-options"] == "DENY", path
        assert headers["content-security-policy"] == "frame-ancestors 'none'", path


def test_review_4_a_malformed_length_gets_an_answer(world):
    server, proxy = world
    for length in ("abc", "-5"):
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as s:
            s.sendall((f"POST /v1/command HTTP/1.1\r\nHost: 127.0.0.1:{proxy.port}\r\n{KEY_HEADER}: {KEY}\r\n"
                       f"Content-Type: application/json\r\nContent-Length: {length}\r\n\r\n").encode())
            answer = b""
            while chunk := s.recv(4096):
                answer += chunk
        head, _, body = answer.partition(b"\r\n\r\n")
        assert head.split(b" ")[1] == b"400", length
        assert json.loads(body)["kind"] == "bad_request"
    assert server.requests == []


def test_lumi_271_the_portrait_is_picked_and_served_locally(tmp_path):
    from PIL import Image

    from desktop.faces import FacePicker
    from desktop.proxy import Proxy

    pack = tmp_path / "faces"
    for n in ("01", "02", "03"):
        (pack / "quiet" / "sad").mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (4, 4)).save(pack / "quiet" / "sad" / f"{n}.png")
    Image.new("RGB", (4, 4)).save(pack / "calm.png")
    (pack / "notes.txt").write_text("not a picture")
    (tmp_path / "secret.png").write_bytes(b"outside the pack")
    server = FakeServer()
    proxy = Proxy(Upstream(server.url, TOKEN), key=KEY, faces=FacePicker(pack))
    proxy.start()
    try:
        status, _, body = request(proxy, "GET", "/face?theme=quiet&emotion=sad&intensity=0.9")
        assert status == 200 and json.loads(body) == {"src": "/faces/quiet/sad/03.png", "theme": "quiet",
                                                       "emotion": "sad", "band": "high"}
        status, headers, png = request(proxy, "GET", "/faces/quiet/sad/03.png", key=False)
        assert status == 200 and headers["content-type"] == "image/png" and png.startswith(b"\x89PNG")
        assert request(proxy, "GET", "/face?emotion=sad", key=False)[0] == 403  # the key, like /v1
        assert request(proxy, "GET", "/face?emotion=sad", headers={"Host": f"evil.example:{proxy.port}"})[0] == 403
        for path in ("/faces/../secret.png", "/faces/%2e%2e/secret.png", "/faces/notes.txt", "/faces/nope.png"):
            assert request(proxy, "GET", path, key=False)[0] == 404, path
        assert server.requests == []  # no portrait question ever reaches the server
    finally:
        proxy.stop()
        server.close()
    bare = Proxy(Upstream(None, ""), key=KEY)
    bare.start()
    try:
        status, _, body = request(bare, "GET", "/face?emotion=calm")
        assert status == 404 and json.loads(body)["kind"] == "no_faces"
    finally:
        bare.stop()


def test_lumi_276_the_emoji_map_is_served_locally():
    from desktop.proxy import Proxy

    server = FakeServer()
    proxy = Proxy(Upstream(server.url, TOKEN), key=KEY, emoji={"joy": ["a", "b", "c"]})
    proxy.start()
    try:
        status, headers, body = request(proxy, "GET", "/emoji")
        assert status == 200 and json.loads(body) == {"joy": ["a", "b", "c"]}
        assert request(proxy, "GET", "/emoji", key=False)[0] == 403
        assert server.requests == []  # the map never comes from, nor goes to, the server
    finally:
        proxy.stop()
        server.close()

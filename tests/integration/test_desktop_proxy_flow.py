"""v2.12.1 LUMI-266 — the desktop window's local proxy against a fake server (``tests/_desktop_world.py``): the
page served with the launch key, the guard (the key, the Host), the forwarding with the token, a streamed body
relayed frame by frame, and the errors the page shows. Loopback only — no real server, no network."""

from __future__ import annotations

import http.client
import json
import socket

import pytest

from desktop.proxy import KEY_HEADER, KEY_META, Upstream
from tests._desktop_world import KEY, PNG, TOKEN, FakeServer, request, start_proxy


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


def test_v41_a_picture_is_relayed_byte_for_byte_with_its_headers(world):
    server, proxy = world
    status, headers, body = request(proxy, "GET", "/v1/files/raw?path=art%2Fp%20q.png")
    assert status == 200 and body == PNG
    assert headers["content-type"] == "image/png"
    assert headers["x-content-type-options"] == "nosniff" and headers["content-disposition"] == "inline"
    assert headers["cache-control"] == "private, max-age=300"
    assert "set-cookie" not in headers  # only the passed headers reach the page
    [seen] = server.requests
    assert seen["path"] == "/v1/files/raw?path=art%2Fp%20q.png"  # the query reaches the server as the page wrote it
    assert seen["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert request(proxy, "POST", "/v1/files/raw", body={})[0] == 404  # GET only


def test_v42_a_thumbnail_and_a_page_of_pictures_are_relayed(world):
    """v4.2 LUMI-307: the thumbnail byte for byte as image/jpeg with its headers; the listing's query as written."""
    from tests._desktop_world import JPEG

    server, proxy = world
    status, headers, body = request(proxy, "GET", "/v1/images/thumb?path=art%2Fp.png")
    assert status == 200 and body == JPEG and headers["content-type"] == "image/jpeg"
    assert headers["x-content-type-options"] == "nosniff" and headers["content-disposition"] == "inline"
    status, _, body = request(proxy, "GET", "/v1/images?limit=100&folder=art&before=MTph")
    assert status == 200 and json.loads(body)["total"] == 1
    assert server.requests[-1]["path"] == "/v1/images?limit=100&folder=art&before=MTph"
    assert server.requests[-1]["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert request(proxy, "POST", "/v1/images/thumb", body={})[0] == 404  # GET only


def test_v43_the_journal_is_relayed_and_nothing_else_below_it(world):
    """v4.3 LUMI-312: the day list and a day by its date reach the server with the token; any other path under
    /v1/journal/ stops at the proxy."""
    server, proxy = world
    status, _, body = request(proxy, "GET", "/v1/journal")
    assert status == 200 and json.loads(body)["days"][0]["date"] == "2026-11-16"
    status, _, body = request(proxy, "GET", "/v1/journal/2026-11-16")
    assert status == 200 and json.loads(body)["date"] == "2026-11-16"
    assert server.requests[-1]["path"] == "/v1/journal/2026-11-16"
    assert server.requests[-1]["headers"]["Authorization"] == f"Bearer {TOKEN}"
    seen = len(server.requests)
    for path in ("/v1/journal/latest", "/v1/journal/2026-11-16/x", "/v1/journal/..%2Fstate"):
        assert request(proxy, "GET", path)[0] == 404, path
    assert request(proxy, "POST", "/v1/journal/2026-11-16", body={})[0] == 404
    assert len(server.requests) == seen  # none of them reached the server


def test_v43_a_page_of_thoughts_is_relayed_with_its_repeated_kinds(world):
    """v4.3 LUMI-313: the query reaches the server as the page wrote it — several kinds, spoken, the cursor."""
    server, proxy = world
    status, _, body = request(proxy, "GET", "/v1/thoughts?limit=100&kind=wonder&kind=learn&spoken=true&before=dDE")
    assert status == 200 and json.loads(body)["stats"]["total"] == 1
    assert server.requests[-1]["path"] == "/v1/thoughts?limit=100&kind=wonder&kind=learn&spoken=true&before=dDE"
    assert server.requests[-1]["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert request(proxy, "POST", "/v1/thoughts", body={})[0] == 404  # GET only


# --- v4.5 LUMI-323: the actions through the proxy, and the Mac's side ------------------------------------------------

@pytest.fixture
def mac(tmp_path):
    server = FakeServer()
    opened, revealed = [], []
    proxy = start_proxy(Upstream(server.url, TOKEN), downloads=tmp_path / "Downloads",
                        opener=opened.append, revealer=revealed.append)
    yield server, proxy, tmp_path / "Downloads", opened, revealed
    server.release.set()
    proxy.stop()
    server.close()


def test_v45_an_upload_passes_up_to_its_own_cap_and_other_routes_keep_theirs(mac):
    server, proxy, *_ = mac
    nine = b"\x89PNG\r\n\x1a\n" + b"x" * (9 * 1024 * 1024)
    status, _, body = request(proxy, "POST", "/v1/files/upload?name=p.png", raw=nine,
                              headers={"Content-Type": "application/octet-stream"})
    assert status == 200 and json.loads(body)["size"] == len(nine)
    assert server.requests[-1]["headers"]["Authorization"] == f"Bearer {TOKEN}"
    eleven = b"x" * (11 * 1024 * 1024)
    assert request(proxy, "POST", "/v1/files/upload?name=big.png", raw=eleven)[0] == 413
    assert request(proxy, "POST", "/v1/files/rename", raw=b"{" + b" " * (2 * 1024 * 1024) + b"}")[0] == 413
    status, _, body = request(proxy, "POST", "/v1/files/trash", body={"path": "notes/x.md"})
    assert status == 200 and json.loads(body)["echo"] == {"path": "notes/x.md"}


def test_v45_a_download_is_saved_into_downloads_never_over_a_file(mac):
    server, proxy, downloads, _, revealed = mac
    status, _, body = request(proxy, "POST", "/local/save", body={"path": "art/p.png"})
    first = json.loads(body)
    assert status == 200 and first["name"] == "p.png" and (downloads / "p.png").read_bytes() == PNG
    assert server.requests[-1]["path"] == "/v1/files/download?path=art%2Fp.png"
    assert server.requests[-1]["headers"]["Authorization"] == f"Bearer {TOKEN}"
    second = json.loads(request(proxy, "POST", "/local/save", body={"path": "art/p.png"})[2])
    assert second["name"] == "p (2).png" and (downloads / "p.png").read_bytes() == PNG
    assert request(proxy, "POST", "/local/reveal", body={"saved": second["saved"]})[0] == 200
    assert revealed == [second["saved"]]
    assert request(proxy, "POST", "/local/reveal", body={"saved": "/etc/passwd"})[0] == 400  # only what it saved
    status, _, body = request(proxy, "POST", "/local/save", body={"path": "missing.md"})
    assert status == 404 and "missing.md" in json.loads(body)["detail"]


def test_v45_only_a_web_address_opens_in_the_browser(mac):
    _, proxy, _, opened, _ = mac
    assert request(proxy, "POST", "/local/open", body={"url": "https://uk.wikipedia.org/wiki/Дощ"})[0] == 200
    for url in ("file:///etc/passwd", "javascript:alert(1)", "notes/x.md"):
        assert request(proxy, "POST", "/local/open", body={"url": url})[0] == 400
    assert opened == ["https://uk.wikipedia.org/wiki/Дощ"]


def test_v45_the_local_routes_need_the_key_and_a_post(mac):
    server, proxy, downloads, opened, _ = mac
    seen = len(server.requests)
    for path, body in (("/local/save", {"path": "art/p.png"}), ("/local/open", {"url": "https://a.b"}),
                       ("/local/reveal", {"saved": "x"})):
        assert request(proxy, "POST", path, body=body, key=False)[0] == 403
        assert request(proxy, "GET", path)[0] == 405
    assert len(server.requests) == seen and opened == [] and not downloads.exists()


def test_v46_her_voice_is_relayed_byte_for_byte_and_the_switch_reaches_the_server(world):
    """v4.6 LUMI-330: a line's audio as audio/mpeg with its headers, the token added by the proxy; the switch's body as
    the page wrote it; nothing else below /v1/voice/ reaches the server."""
    from tests._desktop_world import MP3

    server, proxy = world
    vid = "0123456789abcdef0123456789abcdef"
    status, headers, body = request(proxy, "GET", f"/v1/voice/{vid}")
    assert status == 200 and body == MP3 and headers["content-type"] == "audio/mpeg"
    assert headers["x-content-type-options"] == "nosniff"
    assert server.requests[-1]["path"] == f"/v1/voice/{vid}"
    assert server.requests[-1]["headers"]["Authorization"] == f"Bearer {TOKEN}"
    status, _, body = request(proxy, "POST", "/v1/voice", body={"enabled": False})
    assert status == 200 and json.loads(body) == {"enabled": False, "available": True}
    assert json.loads(server.requests[-1]["body"]) == {"enabled": False}
    seen = len(server.requests)
    for path in (f"/v1/voice/{vid}.mp3", "/v1/voice/latest", f"/v1/voice/{vid}/x"):
        assert request(proxy, "GET", path)[0] == 404, path
    assert request(proxy, "GET", f"/v1/voice/{vid}", key=False)[0] in (401, 403)  # the window's key, always
    assert len(server.requests) == seen


# --- v4.7 LUMI-333: dictation through the proxy, the shell's socket and the real server --------------------------

class _EchoStream:
    """Deepgram's side on the server: each audio frame → an interim; CloseStream → the phrase, then the end."""

    def __init__(self):
        import asyncio

        self.queue = asyncio.Queue()

    async def send_pcm(self, data):
        self.queue.put_nowait({"type": "Results", "is_final": False,
                               "channel": {"alternatives": [{"transcript": "Привіт"}]}})

    async def finish(self):
        self.queue.put_nowait({"type": "Results", "is_final": True, "speech_final": True,
                               "channel": {"alternatives": [{"transcript": "Привіт, світ."}]}})
        self.queue.put_nowait(None)

    async def close(self):
        pass

    async def events(self):
        while (event := await self.queue.get()) is not None:
            yield event


class _Mic:
    def __init__(self):
        import threading

        self.opened = threading.Event()
        self.closed = threading.Event()
        self.on_block = None

    def __call__(self, device, on_block):
        self.on_block = on_block
        self.opened.set()
        return self

    def close(self):
        self.closed.set()


@pytest.fixture
def dictating(tmp_path):
    """The real server app on uvicorn (loopback, an ephemeral port) with a fake streaming recognizer; the proxy with
    the shell's real Dictation (the websockets client) and a fake mic."""
    import threading
    import time

    pytest.importorskip("websockets")
    from core.agent import Core
    from core.llm import MockLLMClient
    from desktop.dictation import Dictation
    from server.__main__ import make_server
    from server.app import create_app
    from server.dictation import DictationSetup
    from state.local_store import JsonRepository
    from voice.stt import MockSTT

    async def factory():
        return _EchoStream()

    core = Core(llm=MockLLMClient(states={"reply": "так", "emotion": "calm", "intensity": 0.5}),
                repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m")
    app = create_app(core, token=TOKEN, env="dev", version="4.7.0",
                     dictation=DictationSetup(stt=MockSTT(""), stream_factory=factory))
    server = make_server(app, host="127.0.0.1", port=0)
    serving = threading.Thread(target=server.run, daemon=True)
    serving.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    upstream = Upstream(f"http://127.0.0.1:{port}", TOKEN)
    mic = _Mic()
    proxy = start_proxy(upstream, dictation=Dictation(upstream, open_mic=mic))
    yield proxy, mic
    proxy.stop()
    server.should_exit = True
    serving.join(5)


def _listen(proxy, events, done):
    conn = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=10)
    conn.request("POST", "/local/dictation/start", body=b"{}",
                 headers={KEY_HEADER: KEY, "Content-Type": "application/json"})
    res = conn.getresponse()
    events.append(("status", res.status))
    for raw in res:
        line = raw.decode().rstrip("\n")
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
            if events[-1]["type"] in ("done", "error"):
                break
    conn.close()
    done.set()


def test_v47_dictation_reaches_the_server_and_the_words_come_back_to_the_page(dictating):
    import threading

    proxy, mic = dictating
    assert json.loads(request(proxy, "GET", "/local/dictation")[2]) == {"available": True, "reason": ""}
    events: list = []
    done = threading.Event()
    threading.Thread(target=_listen, args=(proxy, events, done), daemon=True).start()
    assert mic.opened.wait(10)  # the server said ready → the mic opened
    assert request(proxy, "POST", "/local/dictation/start", body={})[0] == 409  # one at a time
    mic.on_block(b"\x10\x00" * 1600)
    for _ in range(200):
        if any(isinstance(e, dict) and e["type"] == "interim" for e in events):
            break
        threading.Event().wait(0.02)
    assert json.loads(request(proxy, "POST", "/local/dictation/stop", body={})[2]) == {"stopped": True}
    assert done.wait(10)
    assert events[0] == ("status", 200)
    words = [e for e in events[1:] if e["type"] != "level"]
    assert words == [{"type": "ready", "mode": "stream"}, {"type": "interim", "text": "Привіт"},
                     {"type": "final", "text": "Привіт, світ."}, {"type": "done"}]
    assert any(e["type"] == "level" for e in events[1:])
    assert mic.closed.is_set()

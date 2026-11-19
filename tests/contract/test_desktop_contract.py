"""v2.12.1 LUMI-266 — the desktop proxy's contract: it forwards exactly the public API the page uses (every route
one the server really serves, with the same method), the static page comes only from ``desktop/ui/``, and the client
token stays in the proxy (the page and its answers never carry it). The CORS and key guards are pinned live in
``tests/integration/test_desktop_proxy_flow.py``."""

from __future__ import annotations

import re
from pathlib import Path

from desktop import proxy as px

ROOT = Path(__file__).resolve().parents[2]
PAGE_ROUTES = {
    ("GET", "/v1/health"), ("GET", "/v1/state"), ("GET", "/v1/events"),
    ("POST", "/v1/turn"), ("POST", "/v1/turn/stream"), ("POST", "/v1/command"), ("POST", "/v1/directive"),
    ("POST", "/v1/session/new"),
    ("GET", "/v1/files"), ("GET", "/v1/files/text"), ("GET", "/v1/files/raw"),  # v4.1: her sandbox, read-only
    ("GET", "/v1/images"), ("GET", "/v1/images/thumb"),  # v4.2: her pictures and their thumbnails
    ("GET", "/v1/journal"),  # v4.3: her journal's days
    ("GET", "/v1/thoughts"),  # v4.3: her thought stream
    ("GET", "/v1/today"), ("GET", "/v1/schedule"),  # v4.4: her day and her timetable
    ("POST", "/v1/files/upload"), ("POST", "/v1/files/rename"), ("POST", "/v1/files/trash"),  # v4.5: the owner's
    ("POST", "/v1/files/restore"), ("GET", "/v1/files/download"),  # actions
    ("POST", "/v1/voice"),  # v4.6: her voice's switch
}
PAGE_PATTERN_ROUTES = {("GET", "/v1/journal/{date}"),  # v4.3: a day of her journal — the date in the path
                       ("GET", "/v1/voice/{voice_id}")}  # v4.6: a line's audio — its voice id in the path


def _server_routes() -> set[tuple[str, str]]:
    text = (ROOT / "server" / "app.py").read_text(encoding="utf-8")
    return {(m.upper(), path) for m, path in re.findall(r'@app\.(get|post)\("(/v1/[^"]*)"', text)}


def test_the_proxy_forwards_exactly_the_pages_public_routes():
    assert set(px.FORWARDED) == PAGE_ROUTES  # a new route the page needs joins here, and this contract
    assert set(px.FORWARDED) <= _server_routes()  # each one the server really serves, with that method
    assert all(path.startswith("/v1/") for _, path in px.FORWARDED)
    patterns = {(m, route) for m, route, _ in px.FORWARDED_PATTERNS}
    assert patterns == PAGE_PATTERN_ROUTES and patterns <= _server_routes()  # v4.3: each one a route the server serves


def test_the_page_is_only_desktop_ui_and_the_token_stays_in_the_proxy():
    assert px.UI_DIR == ROOT / "desktop" / "ui"
    assert px.ui_file("/../proxy.py") is None and px.ui_file("/../../server/app.py") is None
    assert "Authorization" not in px.PASS_REQUEST and "Cookie" not in px.PASS_REQUEST  # the page can't set them
    # nothing else of the server reaches the page (v4.1: a picture keeps its nosniff and inline disposition)
    assert set(px.PASS_RESPONSE) == {"Content-Type", "Cache-Control", "X-Content-Type-Options", "Content-Disposition"}
    up = px.Upstream("http://192.168.1.197:8766", "TOKEN-never-shown-0123456789")
    assert "TOKEN-never-shown" not in repr(up) and "TOKEN-never-shown" not in repr(px.Proxy(up))


def test_face_files_never_ride_the_server_api():
    """v2.12.2: the portrait is picked and served by the proxy from the client's own pack — the forwarded routes
    stay exactly the page's /v1 set, and nothing about faces is among them."""
    assert set(px.FORWARDED) == PAGE_ROUTES
    assert not any("face" in path for _, path in px.FORWARDED)
    from desktop import faces

    assert faces.BY_BAND == {"low": "02.png", "mid": "01.png", "high": "03.png"}  # the place-faces naming


def test_v41_the_window_reads_her_files_through_get_routes_only():
    """v4.1 LUMI-303: the three read routes are forwarded as GET only — the page has no way to write her sandbox."""
    files = {(m, p) for m, p in px.FORWARDED if p.startswith("/v1/files") and m == "GET"}
    assert files == {("GET", "/v1/files"), ("GET", "/v1/files/text"), ("GET", "/v1/files/raw"),
                     ("GET", "/v1/files/download")}  # v4.5: + the download (the writes are v4.5's POST routes)


def test_v42_the_window_reads_her_pictures_through_get_routes_only():
    """v4.2 LUMI-307: the gallery's two routes are forwarded as GET only, beside the v4.1 three."""
    images = {(m, p) for m, p in px.FORWARDED if p.startswith("/v1/images")}
    assert images == {("GET", "/v1/images"), ("GET", "/v1/images/thumb")}


def test_v43_the_window_reads_her_journal_through_get_routes_only():
    """v4.3 LUMI-312: the day list and one day by its date — GET only, and nothing else under /v1/journal/."""
    assert {(m, p) for m, p in px.FORWARDED if p.startswith("/v1/journal")} == {("GET", "/v1/journal")}
    assert px.forwarded("GET", "/v1/journal") and px.forwarded("GET", "/v1/journal/2026-11-16")
    for path in ("/v1/journal/", "/v1/journal/2026-11-16/x", "/v1/journal/2026-11-16.md", "/v1/journal/../state",
                 "/v1/journal/latest", "/v1/journal/2026-1-16", "/v1/journal/2026-11-16%2F..",
                 "/v1/journal/2026-11-16\n"):
        assert not px.forwarded("GET", path), path
    assert not px.forwarded("POST", "/v1/journal/2026-11-16") and not px.forwarded("POST", "/v1/journal")


def test_v43_the_window_reads_her_thoughts_through_one_get_route():
    """v4.3 LUMI-313: the stream's page, GET only — the page can't write or fire a thought through it."""
    assert {(m, p) for m, p in px.FORWARDED if p.startswith("/v1/thought")} == {("GET", "/v1/thoughts")}
    assert not px.forwarded("POST", "/v1/thoughts") and not px.forwarded("GET", "/v1/thoughts/x")


def test_v44_the_window_reads_her_day_and_timetable_through_get_routes_only():
    """v4.4 LUMI-317: the State's and the Schedule's routes, GET only."""
    assert {(m, p) for m, p in px.FORWARDED if p in ("/v1/today", "/v1/schedule")} == {
        ("GET", "/v1/today"), ("GET", "/v1/schedule")}
    assert not px.forwarded("POST", "/v1/schedule") and not px.forwarded("GET", "/v1/schedule/next")


def test_v45_the_actions_are_forwarded_and_the_local_routes_stay_local():
    """v4.5 LUMI-323: the five routes forwarded with their methods; the save / reveal / open routes never forwarded."""
    writes = {(m, p) for m, p in px.FORWARDED if p.startswith("/v1/files/") and m == "POST"}
    assert writes == {("POST", "/v1/files/upload"), ("POST", "/v1/files/rename"), ("POST", "/v1/files/trash"),
                      ("POST", "/v1/files/restore")}
    assert not any(p.startswith("/local") for _, p in px.FORWARDED)
    assert px.LOCAL_ROUTES == ("/local/save", "/local/reveal", "/local/open")
    assert px.UPLOAD_MAX_BODY > 10 * 1024 * 1024 > px.MAX_BODY
    for url in ("https://uk.wikipedia.org/wiki/Пам’ять", "http://example.com/a?b=c"):
        assert px.is_web_url(url), url
    for url in ("file:///etc/passwd", "javascript:alert(1)", "notes/x.md", "//evil.com", "https://", "ftp://x.y",
                "https://a.b/\nx", ""):
        assert not px.is_web_url(url), url


def test_v46_the_window_reaches_her_voice_through_two_routes_only():
    """v4.6 LUMI-330: a line's audio by a 32-hex id (GET) and the switch (POST); nothing else below /v1/voice/."""
    vid = "0123456789abcdef0123456789abcdef"
    assert px.forwarded("GET", f"/v1/voice/{vid}") and px.forwarded("POST", "/v1/voice")
    for path in ("/v1/voice/", f"/v1/voice/{vid}/x", f"/v1/voice/{vid}.mp3", f"/v1/voice/{vid.upper()}",
                 f"/v1/voice/{vid[:-1]}", "/v1/voice/..%2Fvoice.json", f"/v1/voice/{vid}\n"):
        assert not px.forwarded("GET", path), path
    assert not px.forwarded("POST", f"/v1/voice/{vid}") and not px.forwarded("GET", "/v1/voice")


def test_v47_dictation_is_local_behind_the_key_and_never_forwarded():
    """v4.7 LUMI-333: the four dictation routes are the shell's own — never forwarded to the server (the shell holds
    the socket to /v1/dictation itself); each needs this launch's key and its own method."""
    from desktop.dictation import Dictation
    from tests._desktop_world import FakeServer, request, start_proxy

    assert px.DICTATION_ROUTES == ("/local/dictation", "/local/dictation/start", "/local/dictation/stop",
                                   "/local/dictation/cancel")
    assert not any(p.startswith("/local") or "dictation" in p for _, p in px.FORWARDED)
    assert not px.forwarded("GET", "/v1/dictation")  # the page never reaches the server's socket
    server = FakeServer()
    upstream = px.Upstream(server.url, "T")
    proxy = start_proxy(upstream, dictation=Dictation(upstream, open_mic=lambda *a: None,
                                                      connect=lambda *a: None))
    try:
        for path in px.DICTATION_ROUTES:
            method = "GET" if path == "/local/dictation" else "POST"
            assert request(proxy, method, path, body=None if method == "GET" else {}, key=False)[0] == 403, path
            assert request(proxy, method, path, body=None if method == "GET" else {},
                           headers={"Host": "evil.example:80"})[0] == 403, path
            other = "POST" if method == "GET" else "GET"
            assert request(proxy, other, path, body={} if other == "POST" else None)[0] == 405, path
        assert server.requests == []  # nothing of it reached the server
    finally:
        proxy.stop()
        server.close()

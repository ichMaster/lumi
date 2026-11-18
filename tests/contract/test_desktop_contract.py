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
}
PAGE_PATTERN_ROUTES = {("GET", "/v1/journal/{date}")}  # v4.3: a day of her journal — the date in the path


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
    files = {(m, p) for m, p in px.FORWARDED if p.startswith("/v1/files")}
    assert files == {("GET", "/v1/files"), ("GET", "/v1/files/text"), ("GET", "/v1/files/raw")}


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

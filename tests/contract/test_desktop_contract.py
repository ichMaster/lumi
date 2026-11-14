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
}


def _server_routes() -> set[tuple[str, str]]:
    text = (ROOT / "server" / "app.py").read_text(encoding="utf-8")
    return {(m.upper(), path) for m, path in re.findall(r'@app\.(get|post)\("(/v1/[^"]*)"', text)}


def test_the_proxy_forwards_exactly_the_pages_public_routes():
    assert set(px.FORWARDED) == PAGE_ROUTES  # a new route the page needs joins here, and this contract
    assert set(px.FORWARDED) <= _server_routes()  # each one the server really serves, with that method
    assert all(path.startswith("/v1/") for _, path in px.FORWARDED)


def test_the_page_is_only_desktop_ui_and_the_token_stays_in_the_proxy():
    assert px.UI_DIR == ROOT / "desktop" / "ui"
    assert px.ui_file("/../proxy.py") is None and px.ui_file("/../../server/app.py") is None
    assert "Authorization" not in px.PASS_REQUEST and "Cookie" not in px.PASS_REQUEST  # the page can't set them
    assert set(px.PASS_RESPONSE) == {"Content-Type", "Cache-Control"}  # nothing else of the server reaches the page
    up = px.Upstream("http://192.168.1.197:8766", "TOKEN-never-shown-0123456789")
    assert "TOKEN-never-shown" not in repr(up) and "TOKEN-never-shown" not in repr(px.Proxy(up))


def test_face_files_never_ride_the_server_api():
    """v2.12.2: the portrait is picked and served by the proxy from the client's own pack — the forwarded routes
    stay exactly the page's /v1 set, and nothing about faces is among them."""
    assert set(px.FORWARDED) == PAGE_ROUTES
    assert not any("face" in path for _, path in px.FORWARDED)
    from desktop import faces

    assert faces.BY_BAND == {"low": "02.png", "mid": "01.png", "high": "03.png"}  # the place-faces naming

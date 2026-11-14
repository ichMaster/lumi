"""The desktop window's local proxy (v2.12.1, LUMI-266).

A page in the pywebview window can't call the server directly — the server sends no CORS headers. So the shell runs
this small HTTP server on ``127.0.0.1`` (a free port): it serves the static page from ``desktop/ui/`` and forwards
the public API the page uses (:data:`FORWARDED`) to the configured server, adding ``Authorization: Bearer <token>``
itself. The page holds no token and calls relative ``/v1`` URLs — the same page can later be served by the server
itself (v4.3), unchanged.

**The local guard.** Other apps, and web pages in a browser, can reach ``127.0.0.1`` too. So every ``/v1`` request
needs this launch's random key (:data:`KEY_HEADER`, templated into ``index.html`` as
``<meta name="lumi-desktop-key">``), the ``Host`` header must be this proxy's own ``127.0.0.1:<port>`` (a DNS
rebinding name is refused), and no response carries a CORS header — a cross-origin page can neither send the key
nor read an answer.

**Errors the page can show.** No server configured → ``503 {"kind": "no_server"}``; the server unreachable →
``502 {"kind": "unreachable"}``; the server refused the token → ``401 {"kind": "token_refused"}``. Every other
status and body passes through. A streamed body (``/v1/turn/stream``, ``/v1/events``) is relayed chunk by chunk.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import secrets
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

UI_DIR = Path(__file__).resolve().parent / "ui"
KEY_HEADER = "X-Lumi-Desktop-Key"
KEY_META = "lumi-desktop-key"
# The public API the page uses — nothing else is forwarded (pinned by tests/contract/test_desktop_contract.py).
FORWARDED: frozenset[tuple[str, str]] = frozenset({
    ("GET", "/v1/health"), ("GET", "/v1/state"), ("GET", "/v1/events"),
    ("POST", "/v1/turn"), ("POST", "/v1/turn/stream"), ("POST", "/v1/command"), ("POST", "/v1/directive"),
    ("POST", "/v1/session/new"),
})
PASS_REQUEST = ("Content-Type", "X-Lumi-Client", "Last-Event-ID")  # the page's headers that reach the server
PASS_RESPONSE = ("Content-Type", "Cache-Control")  # the server's headers that reach the page
MAX_BODY = 1_000_000  # a request body larger than this is refused (a typed line is a few KB)
CONNECT_S = 5.0
_log = logging.getLogger("lumi.desktop")


@dataclass(frozen=True)
class Upstream:
    """Where the proxy forwards to: the server's URL and the client token (never shown)."""

    url: str | None
    token: str = field(default="", repr=False)

    @property
    def configured(self) -> bool:
        return bool(self.url) and bool(self.token)


def upstream_from(cfg: Any) -> Upstream:
    """The tree's client settings (``server.host`` / ``server.port`` / ``server.token``) → an :class:`Upstream`."""
    from tui.remote import server_url

    host = str(getattr(cfg, "server_host", "") or "").strip()
    token = str(getattr(cfg, "server_token", "") or "").strip()
    return Upstream(url=server_url(cfg) if host else None, token=token)


def with_key(html: str, key: str) -> str:
    """``index.html`` with this launch's key as ``<meta name="lumi-desktop-key">`` right after ``<head>``."""
    meta = f'<meta name="{KEY_META}" content="{key}">'
    at = html.lower().find("<head>")
    if at < 0:
        return meta + html
    at += len("<head>")
    return html[:at] + meta + html[at:]


def ui_file(path: str, root: Path = UI_DIR) -> Path | None:
    """The file under ``root`` a URL path names (``/`` → ``index.html``); ``None`` outside it or missing."""
    rel = unquote(path).lstrip("/") or "index.html"
    try:
        base = root.resolve()
        target = (base / rel).resolve()
    except (OSError, ValueError):
        return None
    if target != base and base not in target.parents:
        return None
    return target if target.is_file() else None


class Proxy:
    """The local server: the page from ``ui_dir``, ``/v1`` forwarded to ``upstream``. :meth:`start` binds a free
    port on ``127.0.0.1`` and serves on a daemon thread; :meth:`stop` closes it."""

    def __init__(self, upstream: Upstream, *, ui_dir: Path = UI_DIR, key: str | None = None,
                 client: httpx.Client | None = None) -> None:
        self.upstream = upstream
        self.ui_dir = ui_dir
        self.key = key or secrets.token_urlsafe(24)
        self.http = client or httpx.Client(timeout=httpx.Timeout(connect=CONNECT_S, read=None, write=30.0,
                                                                  pool=CONNECT_S))
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __repr__(self) -> str:
        return f"Proxy(upstream={self.upstream.url!r}, port={self.port})"

    @property
    def port(self) -> int | None:
        return self._server.server_address[1] if self._server is not None else None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self) -> str:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(self))
        server.daemon_threads = True
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1},
                                        name="lumi-desktop-proxy", daemon=True)
        self._thread.start()
        return self.url

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        self.http.close()


def _handler_for(proxy: Proxy) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "lumi-desktop"
        sys_version = ""

        def log_message(self, fmt: str, *args: Any) -> None:  # the request line only — never a header
            _log.debug("desktop proxy: " + fmt, *args)

        # --- the guard -----------------------------------------------------------------------------------
        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") == f"127.0.0.1:{proxy.port}"

        def _key_ok(self) -> bool:
            given = self.headers.get(KEY_HEADER, "")
            return bool(given) and secrets.compare_digest(given.encode(), proxy.key.encode())

        # --- answers -------------------------------------------------------------------------------------
        def _json(self, status: int, kind: str, detail: str) -> None:
            body = json.dumps({"kind": kind, "detail": detail}).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _static(self, path: str) -> None:
            target = ui_file(path, proxy.ui_dir)
            if target is None:
                self._json(404, "not_found", "no such file")
                return
            data = target.read_bytes()
            if target.name == "index.html":
                data = with_key(data.decode("utf-8"), proxy.key).encode("utf-8")
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                ctype += "; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        # --- the forwarding --------------------------------------------------------------------------------
        def _forward(self, method: str, path: str, query: str) -> None:
            up = proxy.upstream
            if not up.configured:
                self._json(503, "no_server", "no server is set — server.host / server.port in settings.yaml and "
                                             "server.token in secrets.yaml")
                return
            body = b""
            if method == "POST":
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    self._json(413, "too_large", "the request is too large")
                    return
                body = self.rfile.read(length) if length > 0 else b""
            headers = {name: self.headers[name] for name in PASS_REQUEST if self.headers.get(name)}
            headers["Authorization"] = f"Bearer {up.token}"
            url = f"{up.url}{path}" + (f"?{query}" if query else "")
            started = False
            try:
                with proxy.http.stream(method, url, headers=headers, content=body or None) as res:
                    if res.status_code == 401:
                        res.read()
                        self._json(401, "token_refused", _detail(res) or "the server refused the client token")
                        return
                    self.send_response(res.status_code)
                    for name in PASS_RESPONSE:
                        if res.headers.get(name):
                            self.send_header(name, res.headers[name])
                    self.send_header("Connection", "close")
                    self.end_headers()
                    started = True
                    for chunk in res.iter_raw():  # SSE frames reach the page as the server writes them
                        self.wfile.write(chunk)
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return  # the page went away (a reload, a closed stream) — the upstream closes with the context
            except httpx.TransportError as exc:
                if not started:
                    self._json(502, "unreachable", f"the server at {up.url} can't be reached ({type(exc).__name__})")
                # a stream that breaks midway just ends: the page sees the close and reconnects

        def _route(self, method: str) -> None:
            parts = urlsplit(self.path)
            path = parts.path
            if not self._host_ok():
                self._json(403, "forbidden", "a foreign Host")
                return
            if path.startswith("/v1/") or path == "/v1":
                if not self._key_ok():
                    self._json(403, "forbidden", "a request without this window's key")
                    return
                if (method, path) not in FORWARDED:
                    self._json(404, "not_found", "not a route this window forwards")
                    return
                self._forward(method, path, parts.query)
                return
            if method != "GET":
                self._json(405, "not_allowed", "only GET serves the page")
                return
            self._static(path)

        def do_GET(self) -> None:  # the http.server protocol's method names
            self._route("GET")

        def do_POST(self) -> None:
            self._route("POST")

        def do_PUT(self) -> None:
            self._json(405, "not_allowed", "not allowed")

        do_DELETE = do_PATCH = do_OPTIONS = do_PUT  # no CORS preflight is ever answered

    return Handler


def _detail(res: httpx.Response) -> str:
    try:
        data = res.json()
    except ValueError:
        return ""
    return str(data.get("detail") or "") if isinstance(data, dict) else ""

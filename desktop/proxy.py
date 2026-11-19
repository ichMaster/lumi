"""The desktop window's local proxy (v2.12.1, LUMI-266).

A page in the pywebview window can't call the server directly — the server sends no CORS headers. So the shell runs
this small HTTP server on ``127.0.0.1`` (a free port): it serves the static page from ``desktop/ui/`` and forwards
the public API the page uses (:data:`FORWARDED`, and the routes with a value in their path,
:data:`FORWARDED_PATTERNS`) to the configured server, adding ``Authorization: Bearer <token>``
itself. The page holds no token and calls relative ``/v1`` URLs — the same page can later be served by the server
itself (v6.3, the web client), unchanged.

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
import re
import secrets
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

import httpx

UI_DIR = Path(__file__).resolve().parent / "ui"
KEY_HEADER = "X-Lumi-Desktop-Key"
KEY_META = "lumi-desktop-key"
# The public API the page uses — nothing else is forwarded (pinned by tests/contract/test_desktop_contract.py).
FORWARDED: frozenset[tuple[str, str]] = frozenset({
    ("GET", "/v1/health"), ("GET", "/v1/state"), ("GET", "/v1/events"),
    ("POST", "/v1/turn"), ("POST", "/v1/turn/stream"), ("POST", "/v1/command"), ("POST", "/v1/directive"),
    ("POST", "/v1/session/new"),
    ("GET", "/v1/files"), ("GET", "/v1/files/text"), ("GET", "/v1/files/raw"),  # v4.1: her sandbox, read-only
    ("GET", "/v1/images"), ("GET", "/v1/images/thumb"),  # v4.2: her pictures and their thumbnails, read-only
    ("GET", "/v1/journal"),  # v4.3: her journal's days (the owner's only — the server checks)
    ("GET", "/v1/thoughts"),  # v4.3: her thought stream, through the server's surfacing filter
    ("GET", "/v1/today"), ("GET", "/v1/schedule"),  # v4.4: her day and her timetable, read-only
    # v4.5: the owner's actions on her sandbox (create-only, nothing deleted — the server enforces it) and a download
    ("POST", "/v1/files/upload"), ("POST", "/v1/files/rename"), ("POST", "/v1/files/trash"),
    ("POST", "/v1/files/restore"), ("GET", "/v1/files/download"),
    ("POST", "/v1/voice"),  # v4.6: her voice's switch (the window and Telegram both)
})
# v4.3: a route with a value in its path — (method, the server's route, the one pattern its paths must match whole).
# Nothing else under that prefix is forwarded: /v1/journal/<YYYY-MM-DD> and no other path below /v1/journal/.
FORWARDED_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("GET", "/v1/journal/{date}", re.compile(r"/v1/journal/\d{4}-\d{2}-\d{2}")),
    ("GET", "/v1/voice/{voice_id}", re.compile(r"/v1/voice/[0-9a-f]{32}")),  # v4.6: a line's audio, by its voice id
)


def forwarded(method: str, path: str) -> bool:
    """Whether the page may reach ``method path`` on the server: an exact route, or a path one pattern matches whole."""
    return (method, path) in FORWARDED or any(m == method and rx.fullmatch(path) for m, _, rx in FORWARDED_PATTERNS)
PASS_REQUEST = ("Content-Type", "X-Lumi-Client", "Last-Event-ID")  # the page's headers that reach the server
# the server's headers that reach the page (v4.1: a picture keeps its nosniff and inline disposition)
PASS_RESPONSE = ("Content-Type", "Cache-Control", "X-Content-Type-Options", "Content-Disposition")
NO_FRAMING = (("X-Frame-Options", "DENY"), ("Content-Security-Policy", "frame-ancestors 'none'"))
MAX_BODY = 1_000_000  # a request body larger than this is refused (a typed line is a few KB)
UPLOAD_PATH = "/v1/files/upload"
UPLOAD_MAX_BODY = 10 * 1024 * 1024 + 64 * 1024  # v4.5: a picture from the Mac (10 MB, the server's own cap) and a margin
LOCAL_BODY_MAX = 64 * 1024  # v4.5: the local routes' small JSON bodies
DRAIN_MAX = 64 * 1024 * 1024  # v4.5: a refused body is read and dropped up to this, then the connection just closes
LOCAL_ROUTES = ("/local/save", "/local/reveal", "/local/open")  # v4.5: the Mac's side, behind the window's key
# v4.7: dictation — the shell's mic (GET the status; POST start = the event stream, stop, cancel), the window's key
DICTATION_ROUTES = ("/local/dictation", "/local/dictation/start", "/local/dictation/stop", "/local/dictation/cancel")
CONNECT_S = 5.0
# Read timeouts towards the server, as tui/remote.py keeps them (code review #1): the push channel hears a heartbeat
# every 15 s, so 45 s of silence is a dead stream; a turn may think for minutes, but not forever.
EVENTS_READ_S = 45.0
READ_S = 600.0
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


def is_web_url(url: str) -> bool:
    """v4.5: an ``http`` / ``https`` address with a host — the only kind the window opens in the browser."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc) and not any(ch in url for ch in "\r\n\t ")


def save_new(folder: Path, name: str, data: bytes) -> Path:
    """v4.5: ``data`` written into ``folder`` as a new file — ``name``, else ``name (2)``… — never over another one."""
    from core.actions import clean_name, free_name

    try:
        clean = clean_name(name)
    except Exception:  # noqa: BLE001 — a name the Mac can't take falls back to a plain one
        clean = "download"
    folder.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        target = folder / free_name(folder, clean)
        try:
            with target.open("xb") as fh:
                fh.write(data)
        except FileExistsError:
            continue
        return target
    raise FileExistsError(clean)


def _open_in_browser(url: str) -> None:  # pragma: no cover - the system browser
    import webbrowser

    webbrowser.open(url)


def _reveal_in_finder(path: str) -> None:  # pragma: no cover - Finder
    import subprocess

    subprocess.run(["open", "-R", path], check=False)


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
                 client: httpx.Client | None = None, read_s: float = READ_S,
                 events_read_s: float = EVENTS_READ_S, faces: Any = None,
                 emoji: dict[str, list[str]] | None = None, downloads: Path | None = None,
                 opener: Any = None, revealer: Any = None, dictation: Any = None) -> None:
        self.upstream = upstream
        self.dictation = dictation  # v4.7: a desktop.dictation.Dictation (the Mac's mic), or None
        self.ui_dir = ui_dir
        self.faces = faces  # v2.12.2: a desktop.faces.FacePicker over the client's own pack (None → no portrait)
        self.emoji = emoji or {}  # LUMI-276: the TUI's emotion → emoji map, {emotion: [low, mid, high]}
        # v4.5: where a download lands on the Mac, how a link opens, how a saved file is shown (injectable for tests)
        self.downloads = downloads or Path.home() / "Downloads"
        self.opener = opener or _open_in_browser
        self.revealer = revealer or _reveal_in_finder
        self.saved: set[str] = set()  # the files this proxy saved — the only ones it will reveal
        self.key = key or secrets.token_urlsafe(24)
        self.read_s = read_s
        self.events_read_s = events_read_s
        self.http = client or httpx.Client(timeout=self.timeout_for(""))
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __repr__(self) -> str:
        return f"Proxy(upstream={self.upstream.url!r}, port={self.port})"

    def timeout_for(self, path: str) -> httpx.Timeout:
        """A stalled read ends the relay and frees its thread: 45 s on the push channel, 600 s on a turn."""
        read = self.events_read_s if path == "/v1/events" else self.read_s
        return httpx.Timeout(connect=CONNECT_S, read=read, write=30.0, pool=CONNECT_S)

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
        if self.dictation is not None:  # v4.7: the window is closing — a running dictation ends, the mic closes
            self.dictation.close()
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
            for name, value in NO_FRAMING:  # no other page may frame the chat (code review #3)
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        # --- the portrait (v2.12.2): picked here, served from the local pack — never the server's ----------------
        def _face(self, query: str) -> None:
            picker = proxy.faces
            if picker is None or not picker.available:
                self._json(404, "no_faces", "this client has no face pack")
                return
            q = {k: v[0] for k, v in parse_qs(query).items() if v}
            try:
                intensity = float(q["intensity"]) if q.get("intensity") else None
            except ValueError:
                intensity = None
            got = picker.pick(q.get("theme") or None, q.get("emotion") or "calm", intensity)
            if got is None:
                self._json(404, "no_faces", "no picture for this state in the pack")
                return
            body = json.dumps({"src": "/faces/" + quote(got.rel), "theme": got.theme, "emotion": got.emotion,
                               "band": got.band}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _face_file(self, path: str) -> None:
            picker = proxy.faces
            target = ui_file(path[len("/faces/"):], picker.faces_dir) if picker is not None else None
            if target is None or target.suffix.lower() != ".png":
                self._json(404, "not_found", "no such portrait")
                return
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(data)

        def _drain(self, length: int) -> None:
            """v4.5: discard a refused body (at most :data:`DRAIN_MAX`), so the answer reaches the page."""
            left = min(length, DRAIN_MAX)
            while left > 0:
                chunk = self.rfile.read(min(left, 1 << 16))
                if not chunk:
                    break
                left -= len(chunk)

        # --- v4.5: the Mac's side of the actions --------------------------------------------------------------
        def _local_body(self) -> dict | None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > LOCAL_BODY_MAX:
                self._json(400, "bad_request", "a malformed or too large body")
                return None
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._json(400, "bad_request", "the body is not JSON")
                return None
            if not isinstance(body, dict):
                self._json(400, "bad_request", "the body is not a JSON object")
                return None
            return body

        def _answer(self, status: int, payload: dict) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _local(self, path: str) -> None:
            body = self._local_body()
            if body is None:
                return
            if path == "/local/open":  # an external link, in the default browser — http / https only
                url = str(body.get("url") or "")
                if not is_web_url(url):
                    self._json(400, "bad_url", "only an http or https address opens")
                    return
                proxy.opener(url)
                self._answer(200, {"opened": url})
            elif path == "/local/reveal":  # a file this proxy saved, shown in Finder
                saved = str(body.get("saved") or "")
                if saved not in proxy.saved:
                    self._json(400, "unknown", "not a file this window saved")
                    return
                proxy.revealer(saved)
                self._answer(200, {"revealed": saved})
            else:
                self._save(str(body.get("path") or ""))

        def _save(self, rel: str) -> None:
            """A file of her sandbox → ``~/Downloads`` under a free name (create-only); the server's refusal passes."""
            up = proxy.upstream
            if not up.configured:
                self._json(503, "no_server", "no server is set")
                return
            url = f"{up.url}/v1/files/download?path={quote(rel, safe='')}"
            try:
                res = proxy.http.get(url, headers={"Authorization": f"Bearer {up.token}"},
                                     timeout=proxy.timeout_for("/v1/files/download"))
            except httpx.TransportError as exc:
                self._json(502, "unreachable", f"the server at {up.url} can't be reached ({type(exc).__name__})")
                return
            if res.status_code != 200:
                self._json(res.status_code, "refused", _detail(res) or "the server refused the download")
                return
            name = rel.rstrip("/").rsplit("/", 1)[-1] or "download"
            try:
                saved = save_new(proxy.downloads, name, res.content)
            except OSError as exc:
                self._json(500, "not_saved", f"the file couldn't be saved ({type(exc).__name__})")
                return
            proxy.saved.add(str(saved))
            self._answer(200, {"saved": str(saved), "name": saved.name})

        # --- v4.7: dictation — the shell's mic, the server's words, a stream to the page ----------------------
        def _dictation(self, method: str, path: str) -> None:
            from desktop.dictation import Busy

            dictation = proxy.dictation
            if path == "/local/dictation":
                if method != "GET":
                    self._json(405, "not_allowed", "only GET")
                    return
                ok, why = dictation.available() if dictation is not None else (False, "this window can't record")
                self._answer(200, {"available": ok, "reason": why})
                return
            if method != "POST":
                self._json(405, "not_allowed", "only POST")
                return
            if self._local_body() is None:
                return
            if dictation is None:
                self._json(503, "unavailable", "this window can't record")
                return
            if path == "/local/dictation/stop":
                self._answer(200, {"stopped": dictation.stop()})
                return
            if path == "/local/dictation/cancel":
                self._answer(200, {"cancelled": dictation.cancel()})
                return
            ok, why = dictation.available()
            if not ok:
                self._json(503, "unavailable", why)
                return
            try:
                dictation.claim()
            except Busy:
                self._json(409, "busy", "a dictation is already running")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()

            def emit(message: dict) -> None:
                frame = f"event: {message.get('type')}\ndata: {json.dumps(message, ensure_ascii=False)}\n\n"
                self.wfile.write(frame.encode("utf-8"))
                self.wfile.flush()

            dictation.run(emit)

        # --- the forwarding --------------------------------------------------------------------------------
        def _forward(self, method: str, path: str, query: str) -> None:
            up = proxy.upstream
            if not up.configured:
                self._json(503, "no_server", "no server is set — server.host / server.port in settings.yaml and "
                                             "server.token in secrets.yaml")
                return
            body = b""
            if method == "POST":
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = -1
                if length < 0:  # a malformed length gets an answer, not a traceback (code review #4)
                    self._json(400, "bad_request", "a malformed Content-Length")
                    return
                if length > (UPLOAD_MAX_BODY if path == UPLOAD_PATH else MAX_BODY):  # v4.5: a file from the Mac
                    self._drain(length)  # read what was sent, so the page gets the 413 and not a reset connection
                    self._json(413, "too_large", "the request is too large")
                    return
                body = self.rfile.read(length) if length > 0 else b""
            headers = {name: self.headers[name] for name in PASS_REQUEST if self.headers.get(name)}
            headers["Authorization"] = f"Bearer {up.token}"
            url = f"{up.url}{path}" + (f"?{query}" if query else "")
            started = False
            try:
                with proxy.http.stream(method, url, headers=headers, content=body or None,
                                       timeout=proxy.timeout_for(path)) as res:
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
            if path == "/emoji":  # LUMI-276: the TUI's emoji map, read by the shell (the key; never forwarded)
                if not self._key_ok():
                    self._json(403, "forbidden", "a request without this window's key")
                    return
                body = json.dumps(proxy.emoji, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/face":  # the page asks which portrait; the key like /v1 (nothing leaves this machine)
                if not self._key_ok():
                    self._json(403, "forbidden", "a request without this window's key")
                elif method != "GET":
                    self._json(405, "not_allowed", "only GET")
                else:
                    self._face(parts.query)
                return
            if path.startswith("/faces/"):  # an <img> can't send the key; the Host check above still holds
                if method != "GET":
                    self._json(405, "not_allowed", "only GET")
                else:
                    self._face_file(path)
                return
            if path in DICTATION_ROUTES:  # v4.7: the shell's mic — the key, never forwarded
                if not self._key_ok():
                    self._json(403, "forbidden", "a request without this window's key")
                else:
                    self._dictation(method, path)
                return
            if path in LOCAL_ROUTES:  # v4.5: the Mac's side of the actions — the key, POST only, never forwarded
                if not self._key_ok():
                    self._json(403, "forbidden", "a request without this window's key")
                elif method != "POST":
                    self._json(405, "not_allowed", "only POST")
                else:
                    self._local(path)
                return
            if path.startswith("/v1/") or path == "/v1":
                if not self._key_ok():
                    self._json(403, "forbidden", "a request without this window's key")
                    return
                if not forwarded(method, path):
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

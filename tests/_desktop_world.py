"""v2.12.1 — a fake Лілі server for the desktop proxy's tests: a local ``ThreadingHTTPServer`` on ``127.0.0.1``
that records every request and answers the public routes the way the real server does (JSON, and SSE for the
streams). The events stream sends one frame, then waits for :attr:`FakeServer.release` before the next — so a test
can prove the proxy relays a frame before the stream ends. No real server, no network beyond loopback."""

from __future__ import annotations

import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from desktop.proxy import KEY_HEADER, Proxy, Upstream

TOKEN = "dev-client-TOKEN-0123456789abcdef"
KEY = "launch-key-for-the-tests"


PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256))  # every byte value: a relay must not touch one
JPEG = b"\xff\xd8\xff\xe0" + bytes(range(255, -1, -1))  # v4.2: a thumbnail's bytes, every value again


class FakeServer:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.release = threading.Event()
        world = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, status: int, payload: dict) -> None:
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _sse(self, frames: list[str], *, wait_between: bool = False) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                for n, frame in enumerate(frames):
                    if n and wait_between:
                        world.release.wait(5)
                    self.wfile.write(frame.encode())
                    self.wfile.flush()

            def _record(self, method: str) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode() if length else ""
                world.requests.append({"method": method, "path": self.path, "headers": dict(self.headers),
                                       "body": body})
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    self._send(401, {"detail": "the client token is missing or wrong"})
                    return
                path = self.path.split("?")[0]
                if method == "GET" and path == "/v1/state":
                    self._send(200, {"env": "dev", "version": "2.12.1"})
                elif method == "GET" and path == "/v1/events":
                    self._sse(["event: state\nid: 1\ndata: {\"state\": {}}\n\n",
                               "event: message\nid: 2\ndata: {\"role\": \"lili\", \"text\": \"привіт\"}\n\n"],
                              wait_between=True)
                elif method == "POST" and path == "/v1/turn/stream":
                    self._sse(["event: delta\ndata: {\"text\": \"При\"}\n\n",
                               "event: done\ndata: {\"reply\": \"Привіт\", \"emotion\": \"calm\"}\n\n"])
                elif method == "GET" and path == "/v1/files/raw":  # v4.1: a picture's bytes and their headers
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(PNG)))
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Content-Disposition", "inline")
                    self.send_header("Cache-Control", "private, max-age=300")
                    self.send_header("Set-Cookie", "never=1")  # a header the proxy must not pass
                    self.end_headers()
                    self.wfile.write(PNG)
                elif method == "GET" and path == "/v1/images":  # v4.2: a page of her pictures
                    self._send(200, {"items": [{"path": "art/p.png", "folder": "art", "size": len(PNG),
                                                "modified": "2026-11-17 09:00", "width": 768, "height": 768}],
                                     "next": None, "total": 1, "folders": [{"folder": "art", "count": 1}],
                                     "truncated": False})
                elif method == "GET" and path == "/v1/images/thumb":  # v4.2: a thumbnail's bytes and their headers
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(JPEG)))
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Content-Disposition", "inline")
                    self.send_header("Cache-Control", "private, max-age=300")
                    self.end_headers()
                    self.wfile.write(JPEG)
                elif method == "GET" and path == "/v1/journal":  # v4.3: her journal's days
                    self._send(200, {"days": [{"date": "2026-11-16", "sections": 2, "size": 1400, "stamp": True}],
                                     "truncated": False})
                elif method == "GET" and path.startswith("/v1/journal/"):  # v4.3: one day
                    self._send(200, {"date": path.rsplit("/", 1)[1], "stamp": None,
                                     "sections": [{"time": "00:30", "text": "Тихо."}], "markdown": "# день\n"})
                elif method == "POST" and path == "/v1/command":
                    self._send(200, {"handled": True, "echo": json.loads(body or "{}")})
                else:
                    self._send(404, {"detail": "Not Found"})

            def do_GET(self) -> None:
                self._record("GET")

            def do_POST(self) -> None:
                self._record("POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def start_proxy(upstream: Upstream, ui_dir=None) -> Proxy:
    proxy = Proxy(upstream, key=KEY, **({"ui_dir": ui_dir} if ui_dir is not None else {}))
    proxy.start()
    return proxy


def request(proxy: Proxy, method: str, path: str, *, body: Any = None, headers: dict[str, str] | None = None,
            key: bool = True) -> tuple[int, dict[str, str], bytes]:
    """One request to the proxy → ``(status, headers, body)``."""
    conn = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=10)
    hdrs = dict(headers or {})
    if key:
        hdrs.setdefault(KEY_HEADER, KEY)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    conn.request(method, path, body=data, headers=hdrs)
    res = conn.getresponse()
    out = res.read()
    conn.close()
    return res.status, {k.lower(): v for k, v in res.getheaders()}, out

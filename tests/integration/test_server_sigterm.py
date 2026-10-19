"""`systemctl stop` closes her session (v2.6, LUMI-230) — a real process, a real uvicorn, a real SIGTERM.

uvicorn re-raises the signal it caught after its graceful stop, with the previous handler restored. Before
v2.6 SIGTERM's default then killed the process before ``main()``'s ``finally`` (the brain stop + the session
close); now ``serve()`` maps SIGTERM to Ctrl+C. The child stands in for ``main()``: it serves, then writes a
marker in its ``finally``. Localhost only, an ephemeral port, no model.
"""

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_CHILD = """
import sys
from types import SimpleNamespace
sys.path.insert(0, {root!r})
from fastapi import FastAPI
from server.__main__ import serve
app = FastAPI()
app.state.service = SimpleNamespace(bus=SimpleNamespace(close=lambda: None))
try:
    serve(app, host="127.0.0.1", port={port})
finally:
    open({marker!r}, "w").write("closed")
"""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _listening(port: int) -> bool:
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def test_sigterm_runs_the_shutdown_like_ctrl_c(tmp_path):
    port, marker = _free_port(), tmp_path / "closed"
    child = subprocess.Popen([sys.executable, "-c", _CHILD.format(root=str(ROOT), port=port, marker=str(marker))],
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 15
        while not _listening(port):
            assert child.poll() is None, child.stderr.read().decode()
            assert time.monotonic() < deadline, "the server never started"
            time.sleep(0.05)
        os.kill(child.pid, signal.SIGTERM)  # what `systemctl stop` sends
        assert child.wait(timeout=15) == 0  # not -SIGTERM: the process wasn't killed mid-shutdown
        assert marker.read_text() == "closed"  # main()'s finally ran — the session close would have
    finally:
        if child.poll() is None:
            child.kill()

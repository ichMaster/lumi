"""The Telegram daemons stop on `systemctl stop` like on Ctrl+C (v2.6 code review #1).

Seen live on the box: SIGTERM killed both daemons (exit 143, the units `failed`, no "stopped", the bot session left
open, a sent-but-unsaved reply resent). Now each installs ``sigterm_as_interrupt`` before its asyncio loop. A real
child process runs the daemons' own stop pattern and gets a real SIGTERM; a source pin keeps both daemons on it.
No bot, no network.
"""

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

_CHILD = """
import asyncio, sys
sys.path.insert(0, {root!r})
from core.stopsignal import sigterm_as_interrupt

async def loop():
    print("up", flush=True)
    while True:
        await asyncio.sleep(0.05)

sigterm_as_interrupt()
try:
    asyncio.run(loop())
except KeyboardInterrupt:
    print("stopped", flush=True)
finally:
    print("session closed", flush=True)
"""


def test_sigterm_ends_an_asyncio_daemon_like_ctrl_c():
    child = subprocess.Popen([sys.executable, "-c", _CHILD.format(root=str(ROOT))],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "up"
        time.sleep(0.1)
        os.kill(child.pid, signal.SIGTERM)  # what `systemctl stop` sends
        out, err = child.communicate(timeout=10)
        assert child.returncode == 0, err  # not 143 / -15: a clean stop, the unit ends inactive
        assert out.split() == ["stopped", "session", "closed"]
    finally:
        if child.poll() is None:
            child.kill()


@pytest.mark.parametrize("name", ["inbound", "outbound"])
def test_each_daemon_turns_sigterm_into_its_clean_stop(name):
    src = (ROOT / "telegram" / f"{name}.py").read_text(encoding="utf-8")
    entry = src[src.index("\ndef run("):]  # the daemon's entry point (`python -m telegram.<name>`)
    assert re.search(r"sigterm_as_interrupt\(\).*?asyncio\.run\(_main\(\)\).*?except KeyboardInterrupt:", entry, re.S), \
        f"telegram/{name}.py must install sigterm_as_interrupt() before its loop"

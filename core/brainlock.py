"""One brain per data root (v2.2 code review #1) — the server and the in-process TUI never share a memory.

v2.2 gave each instance two ways to run a brain (``./lumi`` in-process, ``./lumi-server``). Two of them on
one data root would both write the store, both compute the day's mood, both open sessions. Every brain
takes a process-lifetime exclusive ``flock`` on ``<data root>/brain.lock`` right after the env guard; a
second brain gets a readable refusal naming the holder. The OS releases the lock when the process exits —
even on a crash — so a stale lock can't block the next start.

Only brains take it. The Telegram daemons, the voicer and the dictator are bus peers (they share the root
by design), and the viewer, the TUI client and the CLI never write her memory.
"""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

LOCK_NAME = "brain.lock"
_held: dict[Path, int] = {}  # data root → the open fd holding its lock (kept for the process lifetime)


class BrainLockHeld(RuntimeError):
    """Another brain already runs on this data root; ``str(exc)`` is the readable reason."""


def _read_holder(fd: int) -> dict[str, Any]:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        data = os.read(fd, 4096)
        holder = json.loads(data or b"{}")
        return holder if isinstance(holder, dict) else {}
    except (OSError, ValueError):
        return {}


def _refusal(root: Path, holder: dict[str, Any]) -> str:
    role = holder.get("role") or "another process"
    lines = [
        f"Lumi — another brain already runs on this data root ({root}): "
        f"{role} (pid {holder.get('pid', '?')}, since {holder.get('since', '?')}).",
        "One brain per memory — stop it first.",
    ]
    if role == "server":
        lines.append("To talk to the running server, use ./lumi-client.")
    return "\n".join(lines)


def acquire(root: str | Path, role: str, *, clock: Callable[[], datetime] = datetime.now) -> None:
    """Take this root's brain lock for the rest of the process, or raise :class:`BrainLockHeld`."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(root / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        holder = _read_holder(fd)
        os.close(fd)
        raise BrainLockHeld(_refusal(root, holder)) from None
    os.ftruncate(fd, 0)
    os.write(fd, json.dumps({"role": role, "pid": os.getpid(),
                             "since": clock().isoformat(timespec="seconds")}).encode())
    _held[root.resolve()] = fd


def release(root: str | Path) -> None:
    """Give the lock back (the OS does this at exit anyway — this is for tests and orderly handovers)."""
    fd = _held.pop(Path(root).resolve(), None)
    if fd is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def guard_single_brain(cfg: Any, role: str) -> None:
    """For a brain's entry point: hold the data root, or exit readably if another brain does."""
    try:
        acquire(cfg.data_root, role)
    except BrainLockHeld as exc:
        raise SystemExit(str(exc)) from None

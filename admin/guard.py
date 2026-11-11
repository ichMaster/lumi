"""The deploy-agent lock guard (v2.11, LUMI-261 — the v2.10 code review #4).

The deploy agent (``scripts/deploy_agent.py``) holds ``flock(LOCK_EX | LOCK_NB)`` on ``~/lumi/deploy/agent.lock``
for a whole step — a deploy, its stable minute, a rollback. A panel action inside that window changes the container
identity the agent's smoke watches, and a good release is rolled back.

So every panel action runs inside :func:`agent_lock`: it holds the same lock **shared** for the action's duration
(the file is mounted read-only into the panel — ``flock`` needs no write access). While the agent holds it, the action
is refused; while the panel holds it, the agent's own ``LOCK_EX | LOCK_NB`` fails and it says "another deploy step is
running — try again in a minute". A missing lock file (no agent on this box) guards nothing.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

BUSY = "a deploy step is running — try again in a minute; nothing was done"


class Busy(Exception):
    """The deploy agent holds its lock (or the lock can't be read): no panel action now."""


@contextmanager
def agent_lock(path: Path | str | None) -> Iterator[None]:
    """Hold the deploy agent's lock shared for the body; raise :class:`Busy` when the agent holds it."""
    if path is None:
        yield
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        yield
        return
    except OSError as exc:  # fail closed: a lock the panel can't read can't be respected
        raise Busy(f"can't read the deploy agent's lock ({exc.strerror}) — no action until it can") from None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:
            raise Busy(BUSY) from None
        yield
    finally:
        os.close(fd)  # closing the descriptor releases the lock


def agent_busy(path: Path | str | None) -> bool:
    """Whether the deploy agent holds its lock right now (for the page)."""
    try:
        with agent_lock(path):
            return False
    except Busy:
        return True

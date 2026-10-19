"""Stop on SIGTERM exactly as on Ctrl+C (v2.6) — for every long-running Lumi process under systemd.

``systemctl stop`` sends SIGTERM, whose default kills the process outright: no ``finally``, no "stopped" line, exit
143 — the unit ends ``failed``. Handled as SIGINT, it becomes the ``KeyboardInterrupt`` each process already turns
into a clean stop (the server closes her session; the Telegram daemons close the bot session).
"""

from __future__ import annotations

import signal


def sigterm_as_interrupt() -> None:
    """Make SIGTERM raise ``KeyboardInterrupt`` in the main thread, like Ctrl+C (call it from the main thread)."""
    signal.signal(signal.SIGTERM, signal.default_int_handler)

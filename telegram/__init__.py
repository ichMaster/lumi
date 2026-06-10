"""The Telegram bridge (v0.13) — two dumb daemons over the inbox/outbox file bus.

The TUI stays the only brain (it calls ``core.reply``); these daemons never touch the core. The
bot token is a secret (read from ``.env``, never logged or committed). ``aiogram`` is an optional
dependency, imported lazily inside each daemon's ``run()`` so the pure, testable logic imports
without it.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


def get_logger(name: str, log_dir: str | Path | None = None) -> logging.Logger:
    """A configured daemon logger — to **stderr** and (if ``log_dir``) a ``telegram-<name>.log`` file.

    Level from ``LUMI_LOG_LEVEL`` (default ``INFO``). Idempotent — repeated calls don't stack
    handlers. **Never logs the token or message text** (callers log counts + ids only) — the file is
    a safe ``tail -f`` monitoring target.
    """
    log = logging.getLogger(f"lumi.telegram.{name}")
    if log.handlers:  # already configured (idempotent)
        return log
    log.setLevel(getattr(logging, (os.getenv("LUMI_LOG_LEVEL") or "INFO").upper(), logging.INFO))
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(fmt)
    log.addHandler(stream)
    if log_dir is not None:
        d = Path(log_dir)
        d.mkdir(parents=True, exist_ok=True)
        file = logging.FileHandler(d / f"telegram-{name}.log", encoding="utf-8")
        file.setFormatter(fmt)
        log.addHandler(file)
    return log

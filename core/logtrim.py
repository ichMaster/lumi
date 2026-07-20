"""Bounded log retention: trim a log file in-place to entries from the last N days.

Called at TUI startup and on a periodic tick so ``.lumi/*.log|jsonl`` don't grow unbounded. The rewrite
truncates the **same inode** (mode ``"w"``, not a rename), so a process appending concurrently with
``O_APPEND`` (the TUI / daemons) keeps its file descriptor valid and continues at EOF. Best-effort —
never raises; a malformed line is simply kept.

A line's date is read from whichever of the three shapes it has: a leading ``YYYY-MM-DD`` (lumi.log,
telegram-outbound.log), a ``===== YYYY-MM-DD =====`` block marker (mood.log), or a ``"ts": "YYYY-MM-DD``
JSON field (cache-log.jsonl). A line with no date (a traceback/continuation) inherits the prior line's
keep state, so multi-line records aren't split.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

LOG_RETENTION_DAYS = 10  # keep this many days of each log

# The log files the TUI owns/shares in the state dir. outbox.jsonl is a message BUS (pointer-consumed),
# NOT a log — never trimmed here.
LOG_FILES = ("lumi.log", "mood.log", "cache-log.jsonl", "telegram-outbound.log")

_LEADING_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_BLOCK_DATE = re.compile(r"^=====\s*(\d{4}-\d{2}-\d{2})")
_TS_DATE = re.compile(r'"ts":\s*"(\d{4}-\d{2}-\d{2})')


def _line_date(line: str) -> str | None:
    """The ``YYYY-MM-DD`` of a log line, or ``None`` for a continuation line (no date of its own)."""
    for pat in (_LEADING_DATE, _BLOCK_DATE, _TS_DATE):
        m = pat.search(line) if pat is _TS_DATE else pat.match(line)
        if m:
            return m.group(1)
    return None


def trim_log_days(path: str | Path, days: int = LOG_RETENTION_DAYS, *, today: date | None = None) -> bool:
    """Trim ``path`` in-place to lines dated within the last ``days`` days. Returns True if it rewrote.

    No-op (leaves the file and its inode untouched) when the file is missing or nothing is older than the
    cutoff. Continuation lines (no date) inherit the prior line's keep state. Never raises."""
    p = Path(path)
    if not p.is_file():
        return False
    cutoff = ((today or date.today()) - timedelta(days=days)).isoformat()
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except OSError:
        return False
    keep, dropped = False, False
    kept: list[str] = []
    for line in lines:
        d = _line_date(line)
        if d is not None:
            keep = d >= cutoff
        if keep:
            kept.append(line)
        else:
            dropped = True
    if not dropped:
        return False  # already within the window — don't churn the inode
    try:
        with p.open("w", encoding="utf-8") as f:  # truncate same inode; O_APPEND writers continue at EOF
            f.writelines(kept)
    except OSError:
        return False
    return True


def trim_lumi_logs(state_dir: str | Path, days: int = LOG_RETENTION_DAYS) -> int:
    """Trim every known log under ``state_dir`` to the last ``days`` days; return how many were rewritten."""
    d = Path(state_dir)
    return sum(trim_log_days(d / name, days) for name in LOG_FILES)

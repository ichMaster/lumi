"""The audit log (v2.11, LUMI-261): every action the panel takes **or refuses** is one JSON line in
``~/lumi/admin/audit.jsonl`` — ``{ts, action, instance, target, changes, result}``.

``changes`` is what the editor reports (``{key: [old, new]}`` for a plain setting, ``{key: "(secret)"}`` for a
secret — :func:`admin.edit.changed`), so a secret value never reaches the line; a ``redact`` function (the
instances' current secret values → ``•••``) runs over every line as well. ``result`` is ``done``,
``refused: <why>`` or ``failed: <why>``. Lines are appended under a lock; past a size cap the file is rotated
to ``audit.jsonl.1`` (one generation kept).
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from admin.readers import jsonl_tail

AUDIT_FILE = "audit.jsonl"
MAX_BYTES = 2_000_000


def _now() -> datetime:
    return datetime.now().astimezone()


class Audit:
    """Append-only audit lines for one panel (``path`` in its writable folder)."""

    def __init__(self, path: Path, *, clock: Callable[[], datetime] = _now, max_bytes: int = MAX_BYTES) -> None:
        self.path = Path(path)
        self.clock = clock
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    def record(self, action: str, instance: str | None, target: str = "", *, changes: dict | None = None,
               result: str = "done", redact: Callable[[str], str] | None = None) -> dict[str, Any]:
        entry = {"ts": self.clock().isoformat(timespec="seconds"), "action": action, "instance": instance,
                 "target": target, "changes": changes or {}, "result": result}
        line = json.dumps(entry, ensure_ascii=False, default=str)
        if redact is not None:
            line = redact(line)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if self.path.stat().st_size + len(line) > self.max_bytes:
                    os.replace(self.path, self.path.with_name(self.path.name + ".1"))
            except FileNotFoundError:
                pass
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, (line + "\n").encode("utf-8"))
            finally:
                os.close(fd)
        return json.loads(line)

    def read(self, limit: int = 200, instance: str | None = None) -> list[dict[str, Any]]:
        """The newest lines first (the rotated generation after the current one), optionally one instance's."""
        out: list[dict[str, Any]] = []
        for path in (self.path, self.path.with_name(self.path.name + ".1")):
            rows = [r for r in reversed(jsonl_tail(path)) if instance is None or r.get("instance") == instance]
            out += rows
            if len(out) >= limit:
                break
        return out[:limit]

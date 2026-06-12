"""Append-only JSONL FIFO + an id pointer — the file bus between the TUI and peripherals (v0.13).

A producer **appends** records (each gets a **monotonic id**); a consumer **reads** records newer
than its last-seen id and **advances** a tiny pointer file. **One writer + one reader per file** →
no locks. The pointer is the last consumed **id** (not a byte offset), so trimming already-consumed
records (id ≤ pointer) never breaks the consumer — `read_since` only ever returns id > pointer.

This is the shared transport for the Telegram bridge (v0.13), the voicer (v0.14), and later the
dictator (v0.18). Records are `{"id": <int>, "text": <str>, "ts": <iso>, …extra}` — one JSON per line.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.repository import now_iso


def _records(path: Path) -> list[dict]:
    """All records in the file, in order (empty if missing)."""
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _last_id(path: Path) -> int:
    """The highest id in the file (0 if empty). Append-only → the last line holds it."""
    records = _records(path)
    return records[-1]["id"] if records else 0


def append(path: str | Path, text: str, **fields: object) -> int:
    """Append one record with the **next monotonic id** (and an ``ts`` stamp); return that id.

    ``fields`` ride alongside (e.g. ``emotion``, ``theme``). The id continues from the file's
    current high-water-mark, so as long as the file isn't trimmed to empty it stays monotonic.
    """
    p = Path(path)
    next_id = _last_id(p) + 1
    record = {"id": next_id, "text": text, "ts": now_iso(), **fields}
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return next_id


def read_since(path: str | Path, last_id: int) -> list[dict]:
    """The records with ``id > last_id``, oldest first (the consumer's unread tail)."""
    return [r for r in _records(Path(path)) if r["id"] > last_id]


def load_pointer(pos_path: str | Path) -> int:
    """The consumer's last-processed id from its pointer file (0 if none / unreadable)."""
    p = Path(pos_path)
    if not p.is_file():
        return 0
    try:
        return int(p.read_text(encoding="utf-8").strip() or 0)
    except ValueError:
        return 0


def save_pointer(pos_path: str | Path, last_id: int) -> None:
    """Persist the consumer's last-processed id (atomic swap)."""
    p = Path(pos_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(str(last_id), encoding="utf-8")
    tmp.replace(p)

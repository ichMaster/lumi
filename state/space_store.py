"""v3.1 LUMI-279 — the space store: a shared space's lines, day gists and vectors in a SQLite file of their own.

``<data root>/spaces.sqlite`` — beside his relationship store (``store.json`` / ``store.db``), never inside it: no
``user_id`` anywhere, every row keyed by the space (``channel``), so a rollback deletes one file and nothing of his
moves. One connection under one lock (the server's request threads and the room's turn thread share it); every write
is a transaction; a row that no longer reads is skipped, never fatal.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import struct
import threading
from dataclasses import asdict
from pathlib import Path

from core.space import SpaceDigest, SpaceGist, SpaceMessage, ts_epoch

log = logging.getLogger("lumi.space")

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS space_messages ("
    " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    " channel TEXT NOT NULL, role TEXT NOT NULL, source_id TEXT NOT NULL,"
    " session_id TEXT NOT NULL, ts_epoch REAL NOT NULL, data TEXT NOT NULL,"
    " UNIQUE (channel, role, source_id))",
    "CREATE INDEX IF NOT EXISTS idx_space_ts ON space_messages(channel, ts_epoch, seq)",
    "CREATE INDEX IF NOT EXISTS idx_space_session ON space_messages(channel, session_id)",
    "CREATE TABLE IF NOT EXISTS space_vectors ("
    " channel TEXT NOT NULL, role TEXT NOT NULL, source_id TEXT NOT NULL, vector BLOB NOT NULL,"
    " PRIMARY KEY (channel, role, source_id))",
    "CREATE TABLE IF NOT EXISTS space_gists ("
    " channel TEXT NOT NULL, day TEXT NOT NULL, text TEXT NOT NULL, PRIMARY KEY (channel, day))",
    # v3.2: a room session's running digest — overwritten as the session grows (`upto` = the lines it covers)
    "CREATE TABLE IF NOT EXISTS space_digests ("
    " channel TEXT NOT NULL, session_id TEXT NOT NULL, text TEXT NOT NULL, upto INTEGER NOT NULL,"
    " PRIMARY KEY (channel, session_id))",
)


def _pack(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def _record(data: str) -> SpaceMessage | None:
    try:
        return SpaceMessage(**json.loads(data))
    except (TypeError, ValueError):
        log.warning("a space row no longer reads — skipped")
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return -1.0
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb) if na and nb else 0.0


def _scores(query: list[float], vectors: list[list[float]]) -> list[float]:
    """Cosine of ``query`` against each vector — numpy when installed (the ``embed`` extra), else pure Python
    (v3.1 review #10: a year of the room is ~50 K vectors). A vector of another dimension scores -1."""
    try:
        import numpy as np
    except ImportError:
        return [_cosine(query, v) for v in vectors]
    out = [-1.0] * len(vectors)
    same = [i for i, v in enumerate(vectors) if len(v) == len(query)]
    if same:
        mat = np.array([vectors[i] for i in same], dtype=float)
        q = np.array(query, dtype=float)
        denom = np.linalg.norm(mat, axis=1) * np.linalg.norm(q)
        sims = np.divide(mat.dot(q), denom, out=np.zeros(len(same)), where=denom > 0)
        for i, score in zip(same, sims, strict=True):
            out[i] = float(score)
    return out


class SqliteSpaceStore:
    """The :class:`core.space.SpaceStore` over SQLite (WAL)."""

    def __init__(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.path = p
        self._lock = threading.Lock()
        self._db = sqlite3.connect(p, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        with self._db:
            for statement in _SCHEMA:
                self._db.execute(statement)

    # --- lines ----------------------------------------------------------------------------------------------
    def add_lines(self, lines: list[SpaceMessage]) -> list[SpaceMessage]:
        new: list[SpaceMessage] = []
        with self._lock, self._db:
            for line in lines:
                cur = self._db.execute(
                    "INSERT OR IGNORE INTO space_messages (channel, role, source_id, session_id, ts_epoch, data)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (line.channel, line.role, line.source_id, line.session_id, ts_epoch(line.ts),
                     json.dumps(asdict(line), ensure_ascii=False)))
                if cur.rowcount:
                    new.append(line)
        return new

    def get(self, channel: str, source_id: str, role: str) -> SpaceMessage | None:
        with self._lock:
            row = self._db.execute(
                "SELECT data FROM space_messages WHERE channel = ? AND role = ? AND source_id = ?",
                (channel, role, source_id)).fetchone()
        return _record(row[0]) if row else None

    def window(self, channel: str, n: int, *, until_ts: str | None = None) -> list[SpaceMessage]:
        if n <= 0:
            return []
        sql = "SELECT data FROM space_messages WHERE channel = ?"
        args: list = [channel]
        if until_ts is not None:
            sql += " AND ts_epoch <= ?"
            args.append(ts_epoch(until_ts))
        sql += " ORDER BY ts_epoch DESC, seq DESC LIMIT ?"
        args.append(n)
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [r for r in (_record(row[0]) for row in reversed(rows)) if r is not None]

    def latest_session(self, channel: str, day: str) -> tuple[str | None, int]:
        base = f"{channel}-{day}"
        with self._lock:
            rows = self._db.execute(
                "SELECT session_id, COUNT(*) FROM space_messages WHERE channel = ? AND"
                " (session_id = ? OR session_id LIKE ?) GROUP BY session_id",
                (channel, base, base + "-%")).fetchall()
        if not rows:
            return None, 0

        def part(sid: str) -> int:
            tail = sid[len(base):].lstrip("-")
            return int(tail) if tail.isdigit() else 1

        sid, count = max(rows, key=lambda r: part(r[0]))
        return sid, count

    def remove(self, channel: str, source_id: str, role: str) -> bool:
        with self._lock, self._db:
            cur = self._db.execute("DELETE FROM space_messages WHERE channel = ? AND role = ? AND source_id = ?",
                                   (channel, role, source_id))
            self._db.execute("DELETE FROM space_vectors WHERE channel = ? AND role = ? AND source_id = ?",
                             (channel, role, source_id))
        return bool(cur.rowcount)

    # --- vectors --------------------------------------------------------------------------------------------
    def add_vectors(self, channel: str, items: list[tuple[str, str, list[float]]]) -> None:
        with self._lock, self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO space_vectors (channel, role, source_id, vector) VALUES (?, ?, ?, ?)",
                [(channel, role, source_id, _pack(vector)) for source_id, role, vector in items])

    def search_vectors(self, channel: str, query: list[float], k: int) -> list[tuple[float, SpaceMessage]]:
        if k <= 0:
            return []
        with self._lock:
            rows = self._db.execute(
                "SELECT v.vector, m.data FROM space_vectors v JOIN space_messages m ON m.channel = v.channel AND"
                " m.role = v.role AND m.source_id = v.source_id WHERE v.channel = ?", (channel,)).fetchall()
        pairs = [(record, _unpack(blob)) for blob, data in rows if (record := _record(data)) is not None]
        scores = _scores(query, [v for _, v in pairs])
        scored = sorted(zip(scores, (r for r, _ in pairs), strict=True), key=lambda pair: -pair[0])
        return scored[:k]

    # --- gists ----------------------------------------------------------------------------------------------
    def add_gist(self, gist: SpaceGist) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO space_gists (channel, day, text) VALUES (?, ?, ?)",
                             (gist.channel, gist.day, gist.text))

    def gists(self, channel: str) -> list[SpaceGist]:
        with self._lock:
            rows = self._db.execute("SELECT channel, day, text FROM space_gists WHERE channel = ? ORDER BY day",
                                    (channel,)).fetchall()
        return [SpaceGist(*row) for row in rows]

    # --- v3.2: sessions and their running digests ---------------------------------------------------------
    def session_lines(self, channel: str, session_id: str) -> list[SpaceMessage]:
        """A room session's lines, oldest first by ``ts``."""
        with self._lock:
            rows = self._db.execute(
                "SELECT data FROM space_messages WHERE channel = ? AND session_id = ? ORDER BY ts_epoch, seq",
                (channel, session_id)).fetchall()
        return [r for r in (_record(row[0]) for row in rows) if r is not None]

    def session_ids(self, channel: str) -> list[str]:
        with self._lock:
            rows = self._db.execute("SELECT DISTINCT session_id FROM space_messages WHERE channel = ?",
                                    (channel,)).fetchall()
        return sorted(row[0] for row in rows)

    def set_digest(self, digest: SpaceDigest) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO space_digests (channel, session_id, text, upto) VALUES (?, ?, ?, ?)",
                (digest.channel, digest.session_id, digest.text, digest.upto))

    def get_digest(self, channel: str, session_id: str) -> SpaceDigest | None:
        with self._lock:
            row = self._db.execute("SELECT channel, session_id, text, upto FROM space_digests"
                                   " WHERE channel = ? AND session_id = ?", (channel, session_id)).fetchone()
        return SpaceDigest(*row) if row else None

    def close(self) -> None:
        with self._lock:
            self._db.close()

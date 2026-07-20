"""v1.5 (LUMI-193) — the SQLite message journal behind the same ``Repository`` contract.

``SqliteRepository`` keeps the per-TURN hot path — the two ``append_message``s and the closeness
write — in a SQLite database (WAL): one **O(1)** ``INSERT``/UPSERT per record instead of the JSON
store's full 12.9 MB rewrite, and message reads become indexed ``SELECT``s (history is no longer
held in RAM). Everything else (sessions, summaries, facts, digests, thoughts) stays in the inherited
light ``store.json`` — those writes are infrequent (session close, daily), and the file shrinks to
kilobytes once the messages move out.

Rows store the record as JSON (``data`` column), so the schema never chases the ``Message``
dataclass — the same migration shims as the JSON store apply on load. A first open against an
existing ``store.json`` imports its messages into the DB automatically (per session, idempotent) and
then drops them from the JSON file; the full operator migration (vectors included) is
``scripts/migrate_store.py`` (LUMI-195). Selected via ``LUMI_STORE_BACKEND=sqlite`` (default
``json`` → the untouched :class:`~state.local_store.JsonRepository`, byte-identical).
"""

from __future__ import annotations

import json
import sqlite3
import struct
from dataclasses import asdict
from pathlib import Path

from core.repository import Closeness, Message, VectorRecord
from state.local_store import _MESSAGE_FIELDS, JsonRepository, _topk_cosine


def _pack_vector(vector: tuple[float, ...]) -> bytes:
    """float32 little-endian BLOB — ~4× smaller than JSON-text floats (v1.5 LUMI-194)."""
    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack_vector(blob: bytes) -> tuple[float, ...]:
    return struct.unpack(f"<{len(blob) // 4}f", blob)


class SqliteRepository(JsonRepository):
    """A :class:`~core.repository.Repository` with the hot path (messages + closeness) in SQLite."""

    def __init__(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = p.parent / f"{p.stem}.db"
        # One connection, shared across the TUI's worker threads — every public method already runs
        # under the store's RLock (JsonRepository._guard_with_lock), so serialized access is safe.
        self._db = sqlite3.connect(self._db_path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS messages ("
            " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            " session_id TEXT NOT NULL,"
            " user_id TEXT NOT NULL,"
            " data TEXT NOT NULL)"
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id)")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS closeness (user_id TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        # v1.5 (LUMI-194): the vector store — float32 BLOBs, one row per chunk, PK-deduped. Never
        # loaded wholesale into RAM (the JSONL store held ~450 MB resident); queried per search.
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS vectors ("
            " user_id TEXT NOT NULL,"
            " msg_id TEXT NOT NULL,"
            " kind TEXT NOT NULL DEFAULT 'message',"
            " ts TEXT NOT NULL DEFAULT '',"
            " role TEXT NOT NULL DEFAULT '',"
            " text TEXT NOT NULL DEFAULT '',"
            " parent_msg_id TEXT NOT NULL DEFAULT '',"
            " chunk_index INTEGER NOT NULL DEFAULT 0,"
            " vector BLOB NOT NULL,"
            " PRIMARY KEY (user_id, msg_id))"
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_vectors_user_kind ON vectors(user_id, kind)")
        self._db.commit()
        super().__init__(path)  # loads the (light) store.json + wraps public methods in the lock
        self._adopt_json_messages()
        self._adopt_vectors()

    # --- first-open adoption ----------------------------------------------------------------------
    def _adopt_json_messages(self) -> None:
        """Import messages found in ``store.json`` (a pre-SQLite store) into the DB, per session and
        only where the DB has none for that session (idempotent), then drop them from the JSON file
        so it shrinks. The in-memory dict stays empty afterwards — the DB is the source of truth."""
        with self._lock:
            if self._messages:
                for sid, msgs in self._messages.items():
                    have = self._db.execute(
                        "SELECT COUNT(*) FROM messages WHERE session_id = ?", (sid,)
                    ).fetchone()[0]
                    if have == 0:
                        for m in msgs:
                            self._insert_message(m)
                self._db.commit()
                self._messages = {}
                self._persist()  # store.json shrinks — messages now live in the DB
            # Closeness loaded from a pre-SQLite store.json moves into the DB the same way.
            for uid, c in list(self._closeness.items()):
                row = self._db.execute(
                    "SELECT 1 FROM closeness WHERE user_id = ?", (uid,)
                ).fetchone()
                if row is None:
                    self._db.execute(
                        "INSERT INTO closeness (user_id, data) VALUES (?, ?)",
                        (uid, json.dumps(asdict(c), ensure_ascii=False)),
                    )
            self._db.commit()

    # --- persistence ------------------------------------------------------------------------------
    def _persist(self) -> None:
        """The light JSON persist: as the parent, but messages never ride along (they live in the DB).

        The parent reads ``self._messages`` — kept empty here — so the base implementation already
        writes ``"messages": {}``; this override exists only to document the contract."""
        super()._persist()

    def _insert_message(self, message: Message) -> None:
        self._db.execute(
            "INSERT INTO messages (session_id, user_id, data) VALUES (?, ?, ?)",
            (message.session_id, message.user_id, json.dumps(asdict(message), ensure_ascii=False)),
        )

    # --- the hot path: messages -------------------------------------------------------------------
    def append_message(self, message: Message) -> None:
        # O(1): one INSERT — no store.json rewrite, no in-memory history growth.
        self._insert_message(message)
        self._db.commit()

    def load_messages(self, session_id: str) -> list[Message]:
        rows = self._db.execute(
            "SELECT data FROM messages WHERE session_id = ? ORDER BY seq", (session_id,)
        ).fetchall()
        out: list[Message] = []
        for (data,) in rows:
            raw = json.loads(data)
            if "move" in raw:  # the same legacy shim as the JSON store (v1.1 move → intent)
                raw.setdefault("intent", raw.pop("move"))
            out.append(Message(**{k: v for k, v in raw.items() if k in _MESSAGE_FIELDS}))
        return out

    # --- the hot path: closeness ------------------------------------------------------------------
    def set_closeness(self, closeness: Closeness) -> None:
        # O(1): an UPSERT — the per-turn closeness advance no longer rewrites the store.
        self._closeness[closeness.user_id] = closeness  # keep the in-memory mirror current
        self._db.execute(
            "INSERT INTO closeness (user_id, data) VALUES (?, ?)"
            " ON CONFLICT(user_id) DO UPDATE SET data = excluded.data",
            (closeness.user_id, json.dumps(asdict(closeness), ensure_ascii=False)),
        )
        self._db.commit()

    def get_closeness(self, user_id: str) -> Closeness | None:
        row = self._db.execute(
            "SELECT data FROM closeness WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            return None
        return Closeness(**json.loads(row[0]))

    def clear_memory(self, user_id: str) -> None:
        super().clear_memory(user_id)  # summaries/facts/closeness-dict/vectors + the JSON persist
        self._db.execute("DELETE FROM closeness WHERE user_id = ?", (user_id,))
        self._db.execute("DELETE FROM vectors WHERE user_id = ?", (user_id,))
        self._db.commit()

    # --- v1.5 (LUMI-194): the vector store on SQLite (float32 BLOB, per-search reads) -------------
    _VEC_COLS = "user_id, msg_id, kind, ts, role, text, parent_msg_id, chunk_index, vector"

    def _load_vectors_file(self) -> None:
        """Override: NEVER load the JSONL into RAM — the DB is the vector store here. The one-time
        streaming re-pack of an existing JSONL happens in :meth:`_adopt_vectors`."""

    def _adopt_vectors(self) -> None:
        """One-time adoption: legacy in-``store.json`` vectors (a very old store — the parent load
        put them in memory) and the ``<store>.vectors.jsonl`` file are re-packed into the DB —
        **streamed, batched, without re-embedding**. Runs only while the table is empty (idempotent);
        the JSONL is left in place for the operator to archive (`scripts/migrate_store.py`)."""
        with self._lock:
            if self._vectors:  # legacy in-store.json vectors (parent _load migration path)
                for records in self._vectors.values():
                    for r in records:
                        self._insert_vector(r)
                self._db.commit()
                self._vectors, self._vector_ids = {}, {}
            have = self._db.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
            if have or not self._vectors_path.is_file():
                return
            batch = 0
            with self._vectors_path.open(encoding="utf-8") as fh:
                for line in fh:  # streamed — the 450 MB file never sits in RAM
                    if not line.strip():
                        continue
                    try:
                        self._insert_vector(VectorRecord(**json.loads(line)))
                    except Exception:  # noqa: BLE001 — skip a truncated/corrupt line
                        continue
                    batch += 1
                    if batch % 1000 == 0:
                        self._db.commit()
            self._db.commit()

    def _insert_vector(self, r: VectorRecord) -> None:
        self._db.execute(
            f"INSERT OR IGNORE INTO vectors ({self._VEC_COLS}) VALUES (?,?,?,?,?,?,?,?,?)",
            (r.user_id, r.msg_id, r.kind, r.ts, r.role, r.text, r.parent_msg_id,
             r.chunk_index, _pack_vector(r.vector)),
        )

    def add_vectors(self, records: list[VectorRecord]) -> None:
        # O(1) per record: INSERT OR IGNORE (the PK keeps indexing idempotent — re-embeds are no-ops).
        for r in records:
            self._insert_vector(r)
        self._db.commit()

    def has_vector(self, user_id: str, msg_id: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM vectors WHERE user_id = ? AND msg_id = ?", (user_id, msg_id)
        ).fetchone()
        return row is not None

    def reset_vectors(self, model: str) -> None:
        # A model change → different dimensionality → drop all rows for a clean re-index.
        self._db.execute("DELETE FROM vectors")
        self._db.commit()
        super().reset_vectors(model)  # clears the (empty) dicts, truncates the JSONL, keeps the marker

    def search_vectors(
        self, user_id: str, query_vector: list[float], k: int, *, kind: str | None = None
    ) -> list[tuple[float, VectorRecord]]:
        # Isolation: only this user's rows are ever in the candidate set (and one kind when scoped).
        sql = f"SELECT {self._VEC_COLS} FROM vectors WHERE user_id = ?"
        args: tuple = (user_id,)
        if kind is not None:  # v0.36: scope to one memory layer ("message" | "fact")
            sql += " AND kind = ?"
            args += (kind,)
        records = [
            VectorRecord(user_id=u, msg_id=m, kind=kd, ts=ts, role=ro, text=tx,
                         parent_msg_id=p, chunk_index=ci, vector=_unpack_vector(blob))
            for (u, m, kd, ts, ro, tx, p, ci, blob) in self._db.execute(sql, args)
        ]
        return _topk_cosine(list(query_vector), records, k)

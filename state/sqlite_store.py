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
from dataclasses import asdict
from pathlib import Path

from core.repository import Closeness, Message
from state.local_store import _MESSAGE_FIELDS, JsonRepository


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
        self._db.commit()
        super().__init__(path)  # loads the (light) store.json + wraps public methods in the lock
        self._adopt_json_messages()

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
        self._db.commit()

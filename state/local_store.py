"""A local JSON store behind the core's ``Repository`` interface (v0.1).

Persists ``Session`` + ``Message`` records to a single JSON file so a session's
history reloads across a restart. Inspectable by design; SQLite or a server DB
can replace it later without touching the core (it depends only on
``Repository``). Becomes ``user_id``-keyed in v0.2.
"""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path
from uuid import uuid4

from core.repository import Message, Session, now_iso


class JsonRepository:
    """A :class:`~core.repository.Repository` backed by one JSON file."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._sessions: dict[str, Session] = {}
        self._messages: dict[str, list[Message]] = {}
        self._load()

    # --- persistence -----------------------------------------------------
    def _load(self) -> None:
        if not self._path.is_file():
            return
        data = json.loads(self._path.read_text(encoding="utf-8"))
        for sid, raw in data.get("sessions", {}).items():
            self._sessions[sid] = Session(**raw)
        for sid, raws in data.get("messages", {}).items():
            self._messages[sid] = [Message(**raw) for raw in raws]

    def _persist(self) -> None:
        data = {
            "sessions": {sid: asdict(s) for sid, s in self._sessions.items()},
            "messages": {
                sid: [asdict(m) for m in msgs] for sid, msgs in self._messages.items()
            },
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)  # atomic swap

    # --- Repository interface -------------------------------------------
    def create_session(self) -> Session:
        session = Session(id=uuid4().hex, started_at=now_iso())
        self._sessions[session.id] = session
        self._messages.setdefault(session.id, [])
        self._persist()
        return session

    def get_session(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def end_session(self, session_id: str) -> Session | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        ended = replace(session, ended_at=now_iso())
        self._sessions[session_id] = ended
        self._persist()
        return ended

    def append_message(self, message: Message) -> None:
        self._messages.setdefault(message.session_id, []).append(message)
        self._persist()

    def load_messages(self, session_id: str) -> list[Message]:
        return list(self._messages.get(session_id, []))

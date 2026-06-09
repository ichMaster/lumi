"""A local JSON store behind the core's ``Repository`` interface (v0.2).

Persists ``Session`` + ``Message`` records to a single JSON file so a session's
history reloads across a restart. Keyed by ``user_id`` from v0.2 — per-user
records resolve only in their owner's scope. Inspectable by design; SQLite or a
server DB can replace it later without touching the core.
"""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path
from uuid import uuid4

from core.repository import (
    Closeness,
    DaySummary,
    LongTermFact,
    Message,
    Session,
    SessionDigest,
    ShortSummary,
    Thought,
    WeekSummary,
    now_iso,
)
from core.user import DEFAULT_USER_ID


class JsonRepository:
    """A :class:`~core.repository.Repository` backed by one JSON file."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._sessions: dict[str, Session] = {}
        self._messages: dict[str, list[Message]] = {}
        self._summaries: dict[str, list[ShortSummary]] = {}  # by user_id
        self._day_summaries: dict[str, dict[str, DaySummary]] = {}  # user_id -> date -> DaySummary
        self._week_summaries: dict[str, dict[str, WeekSummary]] = {}  # user_id -> week_start -> WeekSummary
        self._facts: dict[str, list[LongTermFact]] = {}  # by user_id
        self._closeness: dict[str, Closeness] = {}  # by user_id (v0.10)
        self._digests: dict[str, SessionDigest] = {}  # by session_id
        self._thoughts: list[Thought] = []  # v0.12: GLOBAL diary — a list, NOT keyed by user_id
        self._load()

    # --- persistence -----------------------------------------------------
    def _load(self) -> None:
        if not self._path.is_file():
            return
        data = json.loads(self._path.read_text(encoding="utf-8"))
        for sid, raw in data.get("sessions", {}).items():
            # Migration shim: pre-v0.2 records lack user_id → default to owner.
            raw.setdefault("user_id", DEFAULT_USER_ID)
            self._sessions[sid] = Session(**raw)
        for sid, raws in data.get("messages", {}).items():
            msgs = []
            for raw in raws:
                raw.setdefault("user_id", DEFAULT_USER_ID)
                msgs.append(Message(**raw))
            self._messages[sid] = msgs
        for uid, raws in data.get("summaries", {}).items():
            # Migration: pre-v0.9 records have no `gist` → default it to "".
            self._summaries[uid] = [ShortSummary(**{"gist": "", **raw}) for raw in raws]
        for uid, byday in data.get("day_summaries", {}).items():
            self._day_summaries[uid] = {d: DaySummary(**raw) for d, raw in byday.items()}
        for uid, byweek in data.get("week_summaries", {}).items():
            self._week_summaries[uid] = {w: WeekSummary(**raw) for w, raw in byweek.items()}
        for uid, raws in data.get("facts", {}).items():
            self._facts[uid] = [LongTermFact(**raw) for raw in raws]
        for uid, raw in data.get("closeness", {}).items():
            self._closeness[uid] = Closeness(**raw)
        for sid, raw in data.get("digests", {}).items():
            self._digests[sid] = SessionDigest(**raw)
        # v0.12: a flat list (global), not a dict-by-user — that's what makes it global.
        self._thoughts = [Thought(**raw) for raw in data.get("thoughts", [])]

    def _persist(self) -> None:
        data = {
            "sessions": {sid: asdict(s) for sid, s in self._sessions.items()},
            "messages": {
                sid: [asdict(m) for m in msgs] for sid, msgs in self._messages.items()
            },
            "summaries": {
                uid: [asdict(s) for s in items] for uid, items in self._summaries.items()
            },
            "day_summaries": {
                uid: {d: asdict(ds) for d, ds in byday.items()}
                for uid, byday in self._day_summaries.items()
            },
            "week_summaries": {
                uid: {w: asdict(ws) for w, ws in byweek.items()}
                for uid, byweek in self._week_summaries.items()
            },
            "facts": {
                uid: [asdict(f) for f in items] for uid, items in self._facts.items()
            },
            "closeness": {uid: asdict(c) for uid, c in self._closeness.items()},
            "digests": {sid: asdict(d) for sid, d in self._digests.items()},
            "thoughts": [asdict(t) for t in self._thoughts],  # global list
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)  # atomic swap

    # --- Repository interface -------------------------------------------
    def create_session(self, user_id: str) -> Session:
        session = Session(id=uuid4().hex, user_id=user_id, started_at=now_iso())
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

    def list_sessions(self, user_id: str) -> list[Session]:
        return [s for s in self._sessions.values() if s.user_id == user_id]

    def append_message(self, message: Message) -> None:
        self._messages.setdefault(message.session_id, []).append(message)
        self._persist()

    def load_messages(self, session_id: str) -> list[Message]:
        return list(self._messages.get(session_id, []))

    def add_summary(self, summary: ShortSummary) -> None:
        self._summaries.setdefault(summary.user_id, []).append(summary)
        self._persist()

    def recent_summaries(self, user_id: str, limit: int = 5) -> list[ShortSummary]:
        return list(self._summaries.get(user_id, []))[-limit:]

    def summaries_since(self, user_id: str, since_date: str) -> list[ShortSummary]:
        # Compare on the date prefix (YYYY-MM-DD) of the stored ISO ts; user-scoped.
        return [s for s in self._summaries.get(user_id, []) if s.ts[:10] >= since_date]

    def set_day_summary(self, day_summary: DaySummary) -> None:
        self._day_summaries.setdefault(day_summary.user_id, {})[day_summary.date] = day_summary
        self._persist()

    def get_day_summary(self, user_id: str, date: str) -> DaySummary | None:
        return self._day_summaries.get(user_id, {}).get(date)

    def day_summaries_since(self, user_id: str, since_date: str) -> list[DaySummary]:
        byday = self._day_summaries.get(user_id, {})
        return [byday[d] for d in sorted(byday) if d >= since_date]

    def set_week_summary(self, week_summary: WeekSummary) -> None:
        self._week_summaries.setdefault(week_summary.user_id, {})[week_summary.week_start] = week_summary
        self._persist()

    def get_week_summary(self, user_id: str, week_start: str) -> WeekSummary | None:
        return self._week_summaries.get(user_id, {}).get(week_start)

    def week_summaries_since(self, user_id: str, since_week: str) -> list[WeekSummary]:
        byweek = self._week_summaries.get(user_id, {})
        return [byweek[w] for w in sorted(byweek) if w >= since_week]

    def add_fact(self, fact: LongTermFact) -> None:
        self._facts.setdefault(fact.user_id, []).append(fact)
        self._persist()

    def facts(self, user_id: str) -> list[LongTermFact]:
        return list(self._facts.get(user_id, []))

    def get_closeness(self, user_id: str) -> Closeness | None:
        return self._closeness.get(user_id)

    def set_closeness(self, closeness: Closeness) -> None:
        self._closeness[closeness.user_id] = closeness
        self._persist()

    def clear_memory(self, user_id: str) -> None:
        self._summaries.pop(user_id, None)
        self._day_summaries.pop(user_id, None)
        self._week_summaries.pop(user_id, None)
        self._facts.pop(user_id, None)
        self._closeness.pop(user_id, None)
        self._persist()

    def get_digest(self, session_id: str) -> SessionDigest | None:
        return self._digests.get(session_id)

    def set_digest(self, digest: SessionDigest) -> None:
        self._digests[digest.session_id] = digest
        self._persist()

    # --- Thought-stream (v0.12) — GLOBAL, not user-keyed ----------------
    def add_thought(self, thought: Thought) -> None:
        self._thoughts.append(thought)
        self._persist()

    def thoughts_since(self, since_iso: str) -> list[Thought]:
        return sorted((t for t in self._thoughts if t.when >= since_iso), key=lambda t: t.when)

    def thoughts_for(self, user_id: str, since_iso: str) -> list[Thought]:
        # Surfacing isolation: only thoughts sparked under this user (A→B never leaks).
        return sorted(
            (t for t in self._thoughts if t.when >= since_iso and t.user_id == user_id),
            key=lambda t: t.when,
        )

    def prune_thoughts(self, before_iso: str) -> None:
        kept = [t for t in self._thoughts if t.when >= before_iso]
        if len(kept) != len(self._thoughts):
            self._thoughts = kept
            self._persist()

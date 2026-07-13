"""A local JSON store behind the core's ``Repository`` interface (v0.2).

Persists ``Session`` + ``Message`` records to a single JSON file so a session's
history reloads across a restart. Keyed by ``user_id`` from v0.2 — per-user
records resolve only in their owner's scope. Inspectable by design; SQLite or a
server DB can replace it later without touching the core.
"""

from __future__ import annotations

import functools
import json
import math
import threading
from dataclasses import asdict, replace
from pathlib import Path
from uuid import uuid4

from core.repository import (
    Closeness,
    DaySummary,
    FactsDigest,
    LongTermFact,
    Message,
    Session,
    SessionDigest,
    ShortSummary,
    Thought,
    VectorRecord,
    WeekSummary,
    now_iso,
)
from core.user import DEFAULT_USER_ID

# The Message dataclass fields — used to drop unknown keys from a legacy store on load
# (e.g. the v1.1 `move`→`intent` rename) so an old record never fails construction.
_MESSAGE_FIELDS = frozenset(Message.__dataclass_fields__)


def _topk_cosine(
    query: list[float], records: list[VectorRecord], k: int
) -> list[tuple[float, VectorRecord]]:
    """Top-``k`` (score, record) by cosine, descending. Uses numpy when installed (the
    ``embed`` extra), else a pure-Python fallback so the store works with no heavy dep."""
    if not records or k <= 0:
        return []
    # Skip any vector whose dim doesn't match the query (e.g. left over from a previous model).
    records = [r for r in records if len(r.vector) == len(query)]
    if not records:
        return []
    try:
        import numpy as np  # optional (the 'embed' extra); fast brute-force cosine
    except ImportError:
        return _topk_cosine_py(query, records, k)
    mat = np.array([r.vector for r in records], dtype=float)
    q = np.array(query, dtype=float)
    denom = np.linalg.norm(mat, axis=1) * np.linalg.norm(q)
    sims = np.divide(mat.dot(q), denom, out=np.zeros(len(records)), where=denom > 0)
    order = np.argsort(-sims, kind="stable")[:k]
    return [(float(sims[i]), records[i]) for i in order]


def _topk_cosine_py(
    query: list[float], records: list[VectorRecord], k: int
) -> list[tuple[float, VectorRecord]]:
    qn = math.sqrt(sum(x * x for x in query))
    scored: list[tuple[float, VectorRecord]] = []
    for r in records:
        rn = math.sqrt(sum(x * x for x in r.vector))
        denom = qn * rn
        dot = sum(a * b for a, b in zip(query, r.vector, strict=False))
        scored.append((dot / denom if denom else 0.0, r))
    scored.sort(key=lambda t: t[0], reverse=True)  # stable → ties keep insertion order
    return scored[:k]


class JsonRepository:
    """A :class:`~core.repository.Repository` backed by one JSON file."""

    def __init__(self, path: str | Path) -> None:
        # The TUI calls the repo from background worker threads (mood, recall backfill) as well as
        # the main thread; a plain JSON store isn't otherwise thread-safe (concurrent _persist raced
        # on the temp file). One re-entrant lock serializes every public method (_guard_with_lock).
        # The v2 server DB handles concurrency natively (ARCHITECTURE §Storage).
        self._lock = threading.RLock()
        self._path = Path(path)
        self._sessions: dict[str, Session] = {}
        self._messages: dict[str, list[Message]] = {}
        self._summaries: dict[str, list[ShortSummary]] = {}  # by user_id
        self._day_summaries: dict[str, dict[str, DaySummary]] = {}  # user_id -> date -> DaySummary
        self._week_summaries: dict[str, dict[str, WeekSummary]] = {}  # user_id -> week_start -> WeekSummary
        self._facts: dict[str, list[LongTermFact]] = {}  # by user_id
        self._facts_digests: dict[str, FactsDigest] = {}  # by user_id (consolidated facts view)
        self._closeness: dict[str, Closeness] = {}  # by user_id (v0.10)
        self._digests: dict[str, SessionDigest] = {}  # by session_id
        self._thoughts: list[Thought] = []  # v0.12: GLOBAL diary — a list, NOT keyed by user_id
        # v0.16 semantic recall. The vectors (thousands of 1024-float arrays) live in a SEPARATE
        # APPEND-ONLY file, never in store.json — so indexing just appends (instant) instead of
        # rewriting the whole store, and per-turn store writes stay small. `_vector_ids` is an
        # in-memory msg_id set per user for O(1) idempotent appends.
        self._vectors: dict[str, list[VectorRecord]] = {}  # by user_id (loaded into memory)
        self._vector_ids: dict[str, set[str]] = {}  # by user_id — for dedup on append
        self._vectors_path = self._path.parent / f"{self._path.stem}.vectors.jsonl"
        self._vector_model = ""  # the embedding model the stored vectors were built with
        self._load()
        self._guard_with_lock()

    def _guard_with_lock(self) -> None:
        """Wrap every public method so it runs under ``_lock`` (read or write).

        Serializes all access to the shared in-memory state + file, so concurrent calls from
        the TUI's worker threads can't race (the temp-file ``replace`` crash) or mutate a dict
        another thread is iterating. ``_persist``/``_load`` (private) run inside these, reentrant.
        """
        for name in dir(type(self)):
            if name.startswith("_") or not callable(getattr(type(self), name)):
                continue
            bound = getattr(self, name)

            @functools.wraps(bound)
            def guarded(*args, _bound=bound, **kwargs):
                with self._lock:
                    return _bound(*args, **kwargs)

            object.__setattr__(self, name, guarded)

    # --- persistence -----------------------------------------------------
    def _load(self) -> None:
        if not self._path.is_file():
            self._load_vectors_file()  # vectors persist separately — load them even on a fresh store
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
                # Migration: v1.1 renamed the reply's `move` field → `intent`. Carry an old
                # value over (if `intent` isn't already set), then drop any keys the current
                # Message no longer knows so a legacy store never fails to load.
                if "move" in raw:
                    raw.setdefault("intent", raw.pop("move"))
                known = {k: v for k, v in raw.items() if k in _MESSAGE_FIELDS}
                msgs.append(Message(**known))
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
        for uid, raw in data.get("facts_digests", {}).items():
            self._facts_digests[uid] = FactsDigest(**raw)
        for uid, raw in data.get("closeness", {}).items():
            self._closeness[uid] = Closeness(**raw)
        for sid, raw in data.get("digests", {}).items():
            self._digests[sid] = SessionDigest(**raw)
        # v0.12: a flat list (global), not a dict-by-user — that's what makes it global.
        self._thoughts = [Thought(**raw) for raw in data.get("thoughts", [])]
        self._vector_model = data.get("vector_model", "")
        # v0.16: vectors load from the append-only JSONL. Legacy stores kept them inside store.json
        # under "vectors" — migrate those to the JSONL once (no re-embedding), then they're dropped
        # from store.json on the next _persist (which no longer writes that key).
        legacy = data.get("vectors")
        if legacy and not self._vectors_path.is_file():
            for raws in legacy.values():
                for raw in raws:
                    self._remember_vector(VectorRecord(**raw))
            self._rewrite_vectors_file()
            self._persist()  # rewrite store.json WITHOUT the now-migrated vectors
        else:
            self._load_vectors_file()

    def _remember_vector(self, record: VectorRecord) -> bool:
        """Add ``record`` to the in-memory store if new (dedup by msg_id). Returns True if added."""
        ids = self._vector_ids.setdefault(record.user_id, set())
        if record.msg_id in ids:
            return False
        ids.add(record.msg_id)
        self._vectors.setdefault(record.user_id, []).append(record)
        return True

    def _load_vectors_file(self) -> None:
        if not self._vectors_path.is_file():
            return
        for line in self._vectors_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                self._remember_vector(VectorRecord(**json.loads(line)))
            except Exception:  # noqa: BLE001 — skip a truncated/corrupt trailing line
                continue

    def _rewrite_vectors_file(self) -> None:
        """Rewrite the whole JSONL from memory (used on clear/migrate; appends are the hot path)."""
        self._vectors_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps(asdict(r), ensure_ascii=False)
            for items in self._vectors.values()
            for r in items
        ]
        tmp = self._vectors_path.with_suffix(self._vectors_path.suffix + ".tmp")
        tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        tmp.replace(self._vectors_path)

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
            "facts_digests": {uid: asdict(d) for uid, d in self._facts_digests.items()},
            "closeness": {uid: asdict(c) for uid, c in self._closeness.items()},
            "digests": {sid: asdict(d) for sid, d in self._digests.items()},
            "thoughts": [asdict(t) for t in self._thoughts],  # global list
            # NB: vectors live in the append-only `<store>.vectors.jsonl`, NOT here — keeping the
            # big 1024-float arrays out of store.json is what makes per-turn writes fast.
            "vector_model": self._vector_model,
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

    def set_fact_core(self, user_id: str, fact: str, core: bool) -> None:
        # v0.36: re-flag a fact's identity-core membership in place (LongTermFact is frozen → replace).
        self._update_fact(user_id, fact, core=core)

    def set_fact_obsolete(self, user_id: str, fact: str, obsolete: bool) -> None:
        # v0.36: mark a fact obsolete in place (kept in the store, excluded from every fact path).
        self._update_fact(user_id, fact, obsolete=obsolete)

    def _update_fact(self, user_id: str, fact: str, **fields: bool) -> None:
        facts = self._facts.get(user_id)
        if not facts:
            return
        changed = False
        for i, f in enumerate(facts):
            if f.fact == fact and any(getattr(f, k) != v for k, v in fields.items()):
                facts[i] = replace(f, **fields)
                changed = True
        if changed:
            self._persist()

    def get_facts_digest(self, user_id: str) -> FactsDigest | None:
        return self._facts_digests.get(user_id)

    def set_facts_digest(self, digest: FactsDigest) -> None:
        self._facts_digests[digest.user_id] = digest
        self._persist()

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
        self._facts_digests.pop(user_id, None)
        self._closeness.pop(user_id, None)
        # v0.16: /forget clears the user's recall vectors too (rewrite the JSONL without them).
        self._vectors.pop(user_id, None)
        self._vector_ids.pop(user_id, None)
        self._rewrite_vectors_file()
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

    # --- Semantic recall / vector store (v0.16) — per-user, isolated ----
    def add_vector(self, record: VectorRecord) -> None:
        self.add_vectors([record])

    def add_vectors(self, records: list[VectorRecord]) -> None:
        # APPEND new records to the JSONL — no full-store rewrite (that's what froze indexing of a
        # large history). Idempotent: a msg_id already present is skipped (re-embedding is a no-op;
        # a genuine model change goes through reset_vectors, which truncates).
        new_lines: list[str] = []
        for record in records:
            if self._remember_vector(record):
                new_lines.append(json.dumps(asdict(record), ensure_ascii=False))
        if not new_lines:
            return
        self._vectors_path.parent.mkdir(parents=True, exist_ok=True)
        with self._vectors_path.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(new_lines) + "\n")

    def has_vector(self, user_id: str, msg_id: str) -> bool:
        return msg_id in self._vector_ids.get(user_id, ())

    def vectors_model(self) -> str:
        return self._vector_model

    def reset_vectors(self, model: str) -> None:
        # A model change means the old vectors have a different dimensionality — drop them all
        # (truncate the JSONL) so backfill re-indexes with the new model.
        self._vectors.clear()
        self._vector_ids.clear()
        self._vector_model = model
        self._vectors_path.parent.mkdir(parents=True, exist_ok=True)
        self._vectors_path.write_text("", encoding="utf-8")  # truncate
        self._persist()  # store.json keeps the model marker

    def search_vectors(
        self, user_id: str, query_vector: list[float], k: int, *, kind: str | None = None
    ) -> list[tuple[float, VectorRecord]]:
        # Isolation: only this user's vectors are ever in the candidate set.
        records = self._vectors.get(user_id, [])
        if kind is not None:  # v0.36: scope to one memory layer ("message" | "fact")
            records = [r for r in records if r.kind == kind]
        return _topk_cosine(list(query_vector), records, k)

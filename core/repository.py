"""The ``Repository`` seam and the v0.1 data shapes.

Memory access goes through this thin interface; the core depends on it, never on
a concrete store, so the v2 server DB is a swap (ARCHITECTURE §Storage). A local
JSON/SQLite store sits behind it in v0.1 (``state/``).

v0.1 is **single-session, no user concept** — ``user_id`` is added in v0.2, when
the data model and the ``Repository`` become user-scoped (every record keyed by
``user_id``, run with a single default ``owner``). The shapes here deliberately
match ARCHITECTURE §Data model minus that field.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

# Who authored a turn. (ARCHITECTURE §Data model: role: "user" | "lili".)
ROLES = ("user", "lili")


def now_iso() -> str:
    """A UTC ISO-8601 timestamp (second precision) for record stamping."""
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Session:
    """One conversation, owned by one user (``user_id`` from v0.2)."""

    id: str
    user_id: str
    started_at: str
    ended_at: str | None = None


@dataclass(frozen=True)
class Message:
    """A single turn, owned by one user. Assistant turns carry the emotion field from v0.3.

    (``user_id`` added in v0.2; ``emotion``/``intensity`` in v0.3; ``intent`` in v1.1.)
    """

    session_id: str
    user_id: str
    role: str
    text: str
    ts: str
    # Assistant turns carry the emotion field from v0.3 (EMOTION.md §3); None on
    # user turns (and on pre-v0.3 stored messages).
    emotion: str | None = None
    intensity: float | None = None
    # v1.1: the intent the arbiter chose for the reply (one of core.intent.INTENTS);
    # None on user turns, pre-v1.1 records, and while the feature is off.
    intent: str | None = None

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"Message.role must be one of {ROLES}, got {self.role!r}")


def make_message(
    session_id: str,
    user_id: str,
    role: str,
    text: str,
    ts: str | None = None,
    *,
    emotion: str | None = None,
    intensity: float | None = None,
    intent: str | None = None,
) -> Message:
    """Build a :class:`Message`, stamping ``ts`` now unless one is given."""
    return Message(
        session_id=session_id,
        user_id=user_id,
        role=role,
        text=text,
        ts=ts or now_iso(),
        emotion=emotion,
        intensity=intensity,
        intent=intent,
    )


@dataclass(frozen=True)
class ShortSummary:
    """A finished session compressed in **two tiers** (v0.9). Per-user (private).

    ``summary`` — the detailed recall (length-scaled); ``gist`` — a one-line headline
    for the "days at a glance" tier. Pre-v0.9 records have no ``gist`` (loaded as "").
    """

    user_id: str
    session_id: str
    summary: str
    gist: str
    ts: str


@dataclass(frozen=True)
class DaySummary:
    """One local day consolidated into ≤4 rows (v0.9.x). Per-user (private).

    Built from that day's per-session ``ShortSummary.gist``s; the "days at a glance"
    tier injects these compact daily digests instead of raw per-session gists.
    ``count`` is how many session-gists it consolidated — when the day's session count
    grows (incl. today), the digest is stale and gets **regenerated** (lazily, at prompt time).
    """

    user_id: str
    date: str       # local day, "YYYY-MM-DD"
    summary: str    # ≤MAX_DAY_ROWS lines (newline-separated)
    count: int      # number of session summaries consolidated (staleness check)
    ts: str         # when it was consolidated


@dataclass(frozen=True)
class WeekSummary:
    """One Mon–Sun week consolidated for memory (date-based recall). Per-user (private).

    Built from that week's per-session ``ShortSummary.summary`` texts — the coarsest of the
    three date-based recall layers (sessions → days → weeks). ``week_start`` is that week's
    Monday ("YYYY-MM-DD"); ``count`` is how many session summaries it consolidated (staleness).
    """

    user_id: str
    week_start: str  # the week's Monday, "YYYY-MM-DD"
    summary: str     # ≤MAX_WEEK_ROWS lines
    count: int       # number of session summaries consolidated (staleness check)
    ts: str          # when it was consolidated


@dataclass(frozen=True)
class LongTermFact:
    """A durable fact about a user, accumulated across sessions. Per-user (private).

    ``core`` (v0.36, additive — old records default ``False``) marks the **identity-core**: the
    facts always injected into the prompt (name, key relationships, hard boundaries, standing
    agreements). It is curated by a one-off backfill + an initial guess at extraction, then
    re-ranked to ``LUMI_FACTS_CORE_MAX`` at session start (boundaries pinned). See ROADMAP §v0.36.
    """

    user_id: str
    fact: str
    meta: str
    confidence: float
    ts: str
    core: bool = False
    obsolete: bool = False  # v0.36: marked stale/duplicate/irrelevant → excluded from every fact path (kept for audit)


@dataclass(frozen=True)
class FactsDigest:
    """A consolidated, compact view of a user's long-term facts — injected into the prompt
    **instead of** all raw facts. Lossy but **non-destructive**: the raw ``LongTermFact``s stay
    in the store; this is only the prompt-injected view. ``count`` is how many raw facts it was
    built from (the staleness check — rebuild when the facts grow past it). Per-user (private)."""

    user_id: str
    summary: str   # the consolidated facts, newline-joined (one fact per line)
    count: int     # number of raw facts consolidated (staleness check)
    ts: str        # when it was built


@dataclass(frozen=True)
class Closeness:
    """Лілі's relationship level with one user (v0.10). Per-user (private).

    ``value`` is the continuous closeness (e.g. 0–100); ``level`` is its 1–5 bucket;
    ``last_ts`` is the last interaction (for time decay). It biases warmth/openness —
    **never competence**. Per-user and isolated (never crosses users).
    """

    user_id: str
    value: float
    level: int
    last_ts: str


@dataclass(frozen=True)
class SessionDigest:
    """A running summary of the earlier part of one session (in-session compaction).

    ``compacted_count`` is how many of the session's oldest messages this digest
    already covers — those are folded out of the verbatim window. Per-session.
    """

    session_id: str
    summary: str
    compacted_count: int
    ts: str


@dataclass(frozen=True)
class Thought:
    """One mental act in Лілі's dated **diary** (v0.12). **GLOBAL — not user-keyed.**

    Every act is stamped with the local ``when`` (the injected clock) and appended in
    order, so the stream reads as a diary. ``kind`` is the directive that made it
    (``think``/``wonder``); ``seeds`` are which states fed it; ``spoken`` records whether
    it graduated to a spoken turn. ``user_id`` is the **originating** user — carried for
    the **surfacing filter only** (a thought sparked with A never surfaces to B), never
    for storage scoping (the store is one global list, not keyed by ``user_id``).
    """

    when: str               # local diary stamp (injected clock), e.g. "2026-06-09T14:30"
    kind: str               # "think" | "wonder" | …
    text: str               # her thought, her voice
    emotion: str            # the locked base-9 enum (for tone/face)
    seeds: tuple[str, ...]  # which states fed it, e.g. ("mood", "need:creation")
    user_id: str            # originating user — for surfacing only, not storage scoping
    spoken: bool = False    # did it graduate to a spoken turn?
    ts: str = ""            # write time (audit)

    def __post_init__(self) -> None:
        # JSON round-trips tuples as lists; coerce back so equality/round-trip hold.
        if not isinstance(self.seeds, tuple):
            object.__setattr__(self, "seeds", tuple(self.seeds))


def make_thought(
    when: str,
    kind: str,
    text: str,
    emotion: str,
    seeds: tuple[str, ...] | list[str],
    user_id: str,
    *,
    spoken: bool = False,
    ts: str | None = None,
) -> Thought:
    """Build a :class:`Thought`, stamping the audit ``ts`` now unless one is given."""
    return Thought(
        when=when, kind=kind, text=text, emotion=emotion,
        seeds=tuple(seeds), user_id=user_id, spoken=spoken, ts=ts or now_iso(),
    )


@dataclass(frozen=True)
class VectorRecord:
    """One embedded **chunk** in the per-user vector store (v0.16 recall; v0.30 chunking).

    ``vector`` is the chunk's embedding; ``msg_id`` is a **stable, content-addressed** id so
    indexing/backfill is **idempotent** — re-embedding never duplicates. ``parent_msg_id`` is the
    id of the **message** this chunk came from and ``chunk_index`` its 0-based ordinal within that
    message (v0.30), so adjacent chunks reassemble into a passage. A **one-chunk** message is the
    v0.16 case: ``chunk_index == 0`` and ``parent_msg_id == msg_id`` (the message id); old records
    that lack ``parent_msg_id`` default it to ``msg_id`` in :meth:`__post_init__` (back-compatible).
    Per-user (private); retrieval runs only over the requesting user's records (the isolation
    invariant). See SEMANTIC_RECALL.md, SEMANTIC_RECALL_CHUNKING.md.
    """

    user_id: str
    msg_id: str
    vector: tuple[float, ...]
    text: str
    ts: str
    role: str
    parent_msg_id: str = ""   # v0.30: the message this chunk came from ("" → defaults to msg_id)
    chunk_index: int = 0      # v0.30: 0-based ordinal within the message
    kind: str = "message"     # v0.36: "message" | "fact" — which memory layer this vector indexes

    def __post_init__(self) -> None:
        # JSON round-trips tuples as lists; coerce back so equality/round-trip hold.
        if not isinstance(self.vector, tuple):
            object.__setattr__(self, "vector", tuple(float(x) for x in self.vector))
        if not self.parent_msg_id:  # back-compat: a one-chunk message is its own parent
            object.__setattr__(self, "parent_msg_id", self.msg_id)


def vector_msg_id(session_id: str, ts: str, role: str, text: str) -> str:
    """A stable content-addressed id for a **message** (idempotent indexing/backfill)."""
    raw = f"{session_id}|{ts}|{role}|{text}"
    return hashlib.blake2b(raw.encode("utf-8"), digest_size=16).hexdigest()


def fact_vector_id(user_id: str, fact: str) -> str:
    """A stable content-addressed id for a **fact** vector (v0.36) — idempotent embedding/backfill."""
    raw = f"fact|{user_id}|{fact}"
    return hashlib.blake2b(raw.encode("utf-8"), digest_size=16).hexdigest()


def chunk_msg_id(parent_msg_id: str, chunk_index: int, text: str) -> str:
    """A stable content-addressed id for a **chunk** (v0.30): a hash of
    ``parent_msg_id|chunk_index|text`` so re-chunking the same message is idempotent."""
    raw = f"{parent_msg_id}|{chunk_index}|{text}"
    return hashlib.blake2b(raw.encode("utf-8"), digest_size=16).hexdigest()


def make_vector_record(
    *, user_id: str, session_id: str, role: str, text: str, ts: str, vector: list[float]
) -> VectorRecord:
    """Build a :class:`VectorRecord`, deriving the content-addressed ``msg_id``."""
    return VectorRecord(
        user_id=user_id,
        msg_id=vector_msg_id(session_id, ts, role, text),
        vector=tuple(float(x) for x in vector),
        text=text,
        ts=ts,
        role=role,
    )


@runtime_checkable
class Repository(Protocol):
    """The storage seam — keyed by ``user_id`` (ARCHITECTURE §Storage).

    Per-user records resolve only in their owner's scope: the **isolation
    invariant** (a record written under user A is never readable in user B's
    context) holds at the data level from v0.2, pinned by a contract test.
    """

    def create_session(self, user_id: str) -> Session:
        """Create and persist a new session owned by ``user_id``."""
        ...

    def get_session(self, session_id: str) -> Session | None:
        """Return the session, or ``None`` if unknown."""
        ...

    def end_session(self, session_id: str) -> Session | None:
        """Mark a session ended; return the updated session (or ``None``)."""
        ...

    def list_sessions(self, user_id: str) -> list[Session]:
        """List a user's sessions (only that user's)."""
        ...

    def append_message(self, message: Message) -> None:
        """Persist one message."""
        ...

    def load_messages(self, session_id: str) -> list[Message]:
        """Load a session's messages in insertion order."""
        ...

    def add_summary(self, summary: ShortSummary) -> None:
        """Persist a session's short summary (per-user)."""
        ...

    def recent_summaries(self, user_id: str, limit: int = 5) -> list[ShortSummary]:
        """The user's most recent short summaries (newest last), capped at ``limit``."""
        ...

    def summaries_since(self, user_id: str, since_date: str) -> list[ShortSummary]:
        """The user's summaries whose ``ts`` date is on/after ``since_date`` (YYYY-MM-DD).

        Source for building per-day consolidations. Newest last; user-scoped (never
        crosses users). ``since_date`` is a local date string (``YYYY-MM-DD``).
        """
        ...

    def set_day_summary(self, day_summary: DaySummary) -> None:
        """Upsert a day's consolidated summary (per-user, keyed by date)."""
        ...

    def get_day_summary(self, user_id: str, date: str) -> DaySummary | None:
        """The user's consolidated summary for ``date`` (YYYY-MM-DD), or ``None``."""
        ...

    def day_summaries_since(self, user_id: str, since_date: str) -> list[DaySummary]:
        """The user's day summaries with ``date`` on/after ``since_date``, oldest first."""
        ...

    def set_week_summary(self, week_summary: WeekSummary) -> None:
        """Upsert a week's consolidated summary (per-user, keyed by Monday ``week_start``)."""
        ...

    def get_week_summary(self, user_id: str, week_start: str) -> WeekSummary | None:
        """The user's consolidated summary for the week starting ``week_start``, or ``None``."""
        ...

    def week_summaries_since(self, user_id: str, since_week: str) -> list[WeekSummary]:
        """The user's week summaries with ``week_start`` on/after ``since_week``, oldest first."""
        ...

    def add_fact(self, fact: LongTermFact) -> None:
        """Persist a durable fact about a user (per-user)."""
        ...

    def facts(self, user_id: str) -> list[LongTermFact]:
        """The user's accumulated long-term facts."""
        ...

    def set_fact_core(self, user_id: str, fact: str, core: bool) -> None:
        """Set the ``core`` (identity-core) flag on the matching fact (v0.36). No-op if absent."""
        ...

    def set_fact_obsolete(self, user_id: str, fact: str, obsolete: bool) -> None:
        """Set the ``obsolete`` flag on the matching fact (v0.36) — excludes it from every fact
        path while keeping it in the store (non-destructive). No-op if absent."""
        ...

    def get_facts_digest(self, user_id: str) -> FactsDigest | None:
        """The user's consolidated facts digest, or ``None`` (rebuilt when facts grow)."""
        ...

    def set_facts_digest(self, digest: FactsDigest) -> None:
        """Upsert the user's facts digest (keyed by ``user_id``)."""
        ...

    def get_closeness(self, user_id: str) -> Closeness | None:
        """The user's relationship-closeness record, or ``None`` (v0.10). Per-user."""
        ...

    def set_closeness(self, closeness: Closeness) -> None:
        """Upsert the user's closeness record (keyed by ``user_id``)."""
        ...

    def clear_memory(self, user_id: str) -> None:
        """Wipe a user's relationship memory (short summaries + long-term facts + vectors).

        Affects only this ``user_id``; the canon and other users are untouched.
        Session messages are not removed; the user's semantic-recall vectors (v0.16) are.
        """
        ...

    # --- Semantic recall / vector store (v0.16) — per-user, isolated ----
    def add_vector(self, record: VectorRecord) -> None:
        """Index one embedded message (per-user). Idempotent by ``msg_id`` (no duplicates)."""
        ...

    def add_vectors(self, records: list[VectorRecord]) -> None:
        """Index many embedded messages at once (one write). Idempotent by ``msg_id``.

        The bulk path for backfill — a per-record persist would be O(n²) I/O over a big store.
        """
        ...

    def vectors_model(self) -> str:
        """The embedding model the stored vectors were built with (``""`` if none/unknown).

        Lets the core detect a model change (different dimensionality) and re-index.
        """
        ...

    def reset_vectors(self, model: str) -> None:
        """Drop **all** vectors and record ``model`` as the current one (on a model change)."""
        ...

    def has_vector(self, user_id: str, msg_id: str) -> bool:
        """Whether this user already has a vector for ``msg_id`` (for incremental/backfill)."""
        ...

    def search_vectors(
        self, user_id: str, query_vector: list[float], k: int, *, kind: str | None = None
    ) -> list[tuple[float, VectorRecord]]:
        """Top-``k`` records by cosine similarity, **scoped to ``user_id``** (descending).

        Runs only over the requesting user's vectors — A's records never surface for B
        (the isolation invariant). A cold/empty store returns ``[]``. ``kind`` (v0.36) filters the
        candidate set to one layer (``"message"`` | ``"fact"``); ``None`` searches all kinds.
        """
        ...

    def get_digest(self, session_id: str) -> SessionDigest | None:
        """The session's running compaction digest, or ``None``."""
        ...

    def set_digest(self, digest: SessionDigest) -> None:
        """Persist (replace) a session's compaction digest."""
        ...

    # --- Thought-stream (v0.12) — GLOBAL, not user-keyed ----------------
    def add_thought(self, thought: Thought) -> None:
        """Append one thought to the **global** dated diary (not keyed by ``user_id``)."""
        ...

    def thoughts_since(self, since_iso: str) -> list[Thought]:
        """All thoughts with ``when`` >= ``since_iso`` (the raw global stream, oldest first)."""
        ...

    def thoughts_for(self, user_id: str, since_iso: str) -> list[Thought]:
        """Thoughts since ``since_iso`` **surfaceable to** ``user_id`` (the isolation read).

        A thought sparked under user A is never returned for user B. Oldest first.
        """
        ...

    def prune_thoughts(self, before_iso: str) -> None:
        """Drop thoughts older than ``before_iso`` — the soft age cap (the core supplies the cutoff)."""
        ...

"""The ``Repository`` seam and the v0.1 data shapes.

Memory access goes through this thin interface; the core depends on it, never on
a concrete store, so the v1 server DB is a swap (ARCHITECTURE §Storage). A local
JSON/SQLite store sits behind it in v0.1 (``state/``).

v0.1 is **single-session, no user concept** — ``user_id`` is added in v0.2, when
the data model and the ``Repository`` become user-scoped (every record keyed by
``user_id``, run with a single default ``owner``). The shapes here deliberately
match ARCHITECTURE §Data model minus that field.
"""

from __future__ import annotations

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

    (``user_id`` added in v0.2; ``emotion``/``intensity`` in v0.3.)
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
    summary: str    # ≤4 lines (newline-separated)
    count: int      # number of session-gists consolidated (staleness check)
    ts: str         # when it was consolidated


@dataclass(frozen=True)
class LongTermFact:
    """A durable fact about a user, accumulated across sessions. Per-user (private)."""

    user_id: str
    fact: str
    meta: str
    confidence: float
    ts: str


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

    def add_fact(self, fact: LongTermFact) -> None:
        """Persist a durable fact about a user (per-user)."""
        ...

    def facts(self, user_id: str) -> list[LongTermFact]:
        """The user's accumulated long-term facts."""
        ...

    def get_closeness(self, user_id: str) -> Closeness | None:
        """The user's relationship-closeness record, or ``None`` (v0.10). Per-user."""
        ...

    def set_closeness(self, closeness: Closeness) -> None:
        """Upsert the user's closeness record (keyed by ``user_id``)."""
        ...

    def clear_memory(self, user_id: str) -> None:
        """Wipe a user's relationship memory (short summaries + long-term facts).

        Affects only this ``user_id``; the canon and other users are untouched.
        Session messages are not removed.
        """
        ...

    def get_digest(self, session_id: str) -> SessionDigest | None:
        """The session's running compaction digest, or ``None``."""
        ...

    def set_digest(self, digest: SessionDigest) -> None:
        """Persist (replace) a session's compaction digest."""
        ...

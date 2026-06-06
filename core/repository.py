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

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"Message.role must be one of {ROLES}, got {self.role!r}")


def make_message(
    session_id: str, user_id: str, role: str, text: str, ts: str | None = None
) -> Message:
    """Build a :class:`Message`, stamping ``ts`` now unless one is given."""
    return Message(
        session_id=session_id, user_id=user_id, role=role, text=text, ts=ts or now_iso()
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

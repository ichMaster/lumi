"""v3 Agora — a **space**: a conversation with several speakers that no user owns (ARCHITECTURE §Spaces).

Its first is Agora, the owner's Matrix room of agents, reached through matrix-agora's bridge (the agora space API v1,
``docs/agora-space-api.json``). This module holds the space's records and the store seam (v3.1 LUMI-279); the room's
own turn path lives here too (LUMI-280).

**Keyed by the space, never ``user_id``.** A space's lines live in a store of their own (``state/space_store.py`` — a
file beside his store, never columns on the relationship tables), so a rollback drops it whole and none of it is ever
readable as anyone's relationship memory (the isolation invariant restated for spaces).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Protocol

SESSION_MAX_LINES = 1000  # a room session rolls by size past this many lines (no setting — a code constant)


@dataclass(frozen=True)
class SpaceMessage:
    """One line of a shared space: a room line with its speaker (``role: "user"``) or her reply (``role: "lili"``).

    ``source_id`` is the line's identity in the space: the room's Matrix ``event_id`` for a room line; for her reply,
    the ``turn_id`` of the line she answered — so a retried turn finds its stored outcome and a retract finds the
    reply. ``ts`` is ISO-8601 with its offset (the room's own clock for an observed line)."""

    channel: str
    session_id: str
    speaker: str
    role: str  # "user" | "lili"
    text: str
    ts: str
    source_id: str
    emotion: str | None = None
    intensity: float | None = None


@dataclass(frozen=True)
class SpaceGist:
    """A space's day in one or two sentences (written at the session's roll — v3.2)."""

    channel: str
    day: str  # YYYY-MM-DD, her local day
    text: str


def local_day(ts: str, tz: tzinfo | None) -> str:
    """Her local day (``YYYY-MM-DD``) of an ISO timestamp — a line's session follows its own ``ts``, not its
    arrival, so a late line lands in its own day. A naive or unparsable ``ts`` keeps its own date / ``""``."""
    try:
        when = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return ""
    if when.tzinfo is not None and tz is not None:
        when = when.astimezone(tz)
    return when.date().isoformat()


def session_id_for(channel: str, day: str, part: int = 1) -> str:
    """The room's session of a local day: ``agora-2026-11-16``; rolled by size → ``agora-2026-11-16-2``."""
    base = f"{channel}-{day}"
    return base if part <= 1 else f"{base}-{part}"


def assign_session(store: SpaceStore, channel: str, ts: str, tz: tzinfo | None, *, pending: dict[str, int] | None = None,
                   max_lines: int = SESSION_MAX_LINES) -> str:
    """The room's session for a line at ``ts``: its local day's newest session, opened by the day's first line and
    rolled by size (``…-2`` once ``max_lines`` are in it). His sessions never enter into it. ``pending`` counts the
    lines of a batch not yet stored (session id → lines), so a batch past the size rolls too."""
    day = local_day(ts, tz)
    sid, count = store.latest_session(channel, day)
    if sid is None:
        sid, count = session_id_for(channel, day), 0
    count += (pending or {}).get(sid, 0)
    if count >= max_lines:  # a batch is at most 200 lines, so it rolls at most once
        tail = sid[len(f"{channel}-{day}"):].lstrip("-")
        sid = session_id_for(channel, day, (int(tail) if tail.isdigit() else 1) + 1)
    return sid


def ts_epoch(ts: str) -> float:
    """A sortable instant for an ISO timestamp (lines are ordered by ``ts``, not by arrival — Q1). A naive ``ts`` is
    read as UTC; an unparsable one sorts first."""
    try:
        when = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return 0.0
    if when.tzinfo is None:
        from datetime import UTC

        when = when.replace(tzinfo=UTC)
    return when.timestamp()


class SpaceStore(Protocol):
    """The seam the space depends on (the SQLite implementation is ``state.space_store.SqliteSpaceStore``)."""

    def add_lines(self, lines: list[SpaceMessage]) -> list[SpaceMessage]:
        """Store the lines new to the space (by ``channel`` + ``role`` + ``source_id``); return the new ones."""
        ...

    def get(self, channel: str, source_id: str, role: str) -> SpaceMessage | None: ...

    def window(self, channel: str, n: int, *, until_ts: str | None = None) -> list[SpaceMessage]:
        """The last ``n`` lines of the space by ``ts`` (oldest first), optionally up to ``until_ts``."""
        ...

    def latest_session(self, channel: str, day: str) -> tuple[str | None, int]:
        """The newest session of that day and how many lines it holds — ``(None, 0)`` when the day has none."""
        ...

    def remove(self, channel: str, source_id: str, role: str) -> bool:
        """Drop a line and its vector (v3.3's retract); whether a line was there."""
        ...

    def add_vectors(self, channel: str, items: list[tuple[str, str, list[float]]]) -> None:
        """``(source_id, role, vector)`` for lines of the space."""
        ...

    def search_vectors(self, channel: str, query: list[float], k: int) -> list[tuple[float, SpaceMessage]]: ...

    def add_gist(self, gist: SpaceGist) -> None: ...

    def gists(self, channel: str) -> list[SpaceGist]: ...

    def close(self) -> None: ...

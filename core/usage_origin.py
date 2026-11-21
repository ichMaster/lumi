"""v5.1 (LUMI-340): what started a paid call — the **origin** and the **channel** every usage record carries.

The core records its model calls (``cache-log.jsonl``) and stores the messages of a turn; neither knows who asked.
The entry point that starts the work — the server's turn, the brain's Telegram line, a scheduled row, an idle think,
a typed directive, the room — says it once, for the work's duration, with :func:`usage_origin`. The core reads it at
record time (:func:`for_call`, :func:`current`), so it stays interface-independent and no signature fans out.

The value is a :class:`contextvars.ContextVar`: it is set per thread (the server's turn runner, the brain's tick) and
copied into ``asyncio.to_thread``; a thread started inside the work doesn't inherit it, so a record written there has
no origin, unless its kind is housekeeping (then the kind alone names it).

**Origins** (ADMIN_PANEL_II §Every paid call): ``turn`` (the owner's turn, with its channel), ``directive:<name>`` (a
``%directive`` the owner typed), ``schedule:<row>`` (a scheduled row), ``idle`` (the v0.12 idle think and the v0.4
nudge, which run outside the schedule), ``room`` and ``initiative`` (the Agora space), and ``housekeeping:<what>``
(the mood, the session start and close, the compaction, the facts — whatever started the work around them).

**Channels:** ``window``, ``tui``, ``cli``, ``api`` (a client that names no known prefix), ``telegram``, ``agora``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

# The client id's prefix → the channel. Every client names itself in ``X-Lumi-Client`` (v2.4): the window
# ``desktop-<hex>``, the terminal client ``tui-<hex>`` and the CLI ``cli-<hex>`` (both since v5.1).
CHANNEL_PREFIXES: tuple[tuple[str, str], ...] = (("desktop-", "window"), ("tui-", "tui"), ("cli-", "cli"))
CHANNEL_API = "api"  # a missing or unprefixed id
CHANNEL_TELEGRAM = "telegram"
CHANNEL_AGORA = "agora"
CHANNEL_TUI = "tui"

ORIGIN_TURN = "turn"
ORIGIN_IDLE = "idle"
ORIGIN_ROOM = "room"
ORIGIN_INITIATIVE = "initiative"

# The model calls that are housekeeping whatever started the work around them (the ``kind`` of a cache-log record).
HOUSEKEEPING_KINDS = frozenset({"mood", "session-start", "session-close", "compaction", "fact"})


@dataclass(frozen=True)
class UsageOrigin:
    """What started the current work: the origin (``""`` when no entry point said) and its channel (``""`` if none)."""

    origin: str = ""
    channel: str = ""


_NONE = UsageOrigin()
_current: ContextVar[UsageOrigin] = ContextVar("lumi_usage_origin", default=_NONE)


def channel_of(client_id: str | None) -> str:
    """The channel a client id names by its prefix; a missing or unprefixed id is ``api``."""
    cid = (client_id or "").strip()
    for prefix, channel in CHANNEL_PREFIXES:
        if cid.startswith(prefix):
            return channel
    return CHANNEL_API


def directive(name: str) -> str:
    """The origin of a ``%directive`` the owner typed."""
    return f"directive:{name}"


def schedule(row: str) -> str:
    """The origin of a scheduled row (its name, else its directive)."""
    return f"schedule:{row}"


def housekeeping(what: str) -> str:
    """The origin of a housekeeping call."""
    return f"housekeeping:{what}"


def current() -> UsageOrigin:
    """The origin and the channel of the work in progress (empty when no entry point set them)."""
    return _current.get()


def for_call(kind: str) -> UsageOrigin:
    """The origin a model call of ``kind`` records: ``housekeeping:<kind>`` for a housekeeping kind (keeping the
    work's channel), else the work's own origin."""
    cur = _current.get()
    if kind in HOUSEKEEPING_KINDS:
        return UsageOrigin(housekeeping(kind), cur.channel)
    return cur


@contextmanager
def usage_origin(origin: str, channel: str = "") -> Iterator[UsageOrigin]:
    """Set the origin and the channel for the work inside the ``with``, restoring the previous ones after it — even
    when the work raises."""
    value = UsageOrigin(origin, channel)
    token = _current.set(value)
    try:
        yield value
    finally:
        _current.reset(token)

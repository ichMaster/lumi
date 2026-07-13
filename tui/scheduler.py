"""In-TUI thought scheduler (v0.42 LUMI-167).

The clock that decides **when** a directive fires — an in-process module of the TUI (the only brain),
**no daemon, no bus**. It owns the schedule + the last-fired ``schedule.state`` and, on a tick, picks the
entries that are :func:`core.schedule.due` (applying the quiet-hours veto + per-day caps), then the TUI
runs each **directly** through ``Core.run_directive`` (the ``%``-router, not the reply path). On startup a
**catch-up pass** fires fixed-time entries missed while the TUI was closed (within a window).

The planning here is **pure and stateful-but-testable** (no Textual, no timers, no model): the TUI glue is
just ``set_interval → scheduler.due_now() → run_directive`` off-thread. See features/THOUGHT_SCHEDULER.md.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from core.nudge import _in_quiet_hours
from core.schedule import (
    ScheduleEntry,
    _cron_match,
    due,
    last_fired_of,
    load_state,
    save_state,
)
from core.thoughts import should_graduate

_log = logging.getLogger("lumi.scheduler")

# The idle-muse family that can graduate to a spoken self-turn (she speaks first) — the v0.42 LUMI-169
# subsuming of the v0.4 idle nudge. Other directives surface silently (a Thought) or via their own tool.
_SPOKEN_DIRECTIVES = ("think", "wonder")


def graduates(directive: str, seed_n: int, ratio: float) -> bool:
    """Whether a scheduled idle-muse fire graduates to a spoken turn (~``ratio`` of the time,
    deterministic per ``seed_n`` — the migrated nudge behavior)."""
    return directive in _SPOKEN_DIRECTIVES and should_graduate(seed_n, ratio)


class TickService:
    """The fast in-TUI tick (v0.42 LUMI-168) — for **ephemeral code handlers**, not model directives.

    A registered handler is a **zero-arg callback** doing silent bookkeeping (**no `Thought`, no model
    call**) — the home of the future `%update_state` (v1.6 needs / v1.8 inner-life: a split-invariant
    advance-to-`now`). Fire-and-forget: a handler exception is swallowed (never breaks the UI); a backlog
    **collapses to one run** (a re-entrant tick is skipped, never queued); a missed tick is a **no-op**
    (nothing runs while the TUI is closed — the work is idempotent advance-to-`now`, so a gap is harmless).

    v1.6 just calls :meth:`register` with its callback — this ships the mechanism + the seam.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Callable[[], None]] = {}
        self._running = False

    def register(self, name: str, handler: Callable[[], None]) -> None:
        """Register (or replace) a code handler by name — a zero-arg callback, not a model directive."""
        self._handlers[name] = handler

    def tick(self) -> None:
        """Run every registered handler once. Re-entrant ticks are dropped (a backlog collapses to one);
        a handler that raises is logged and swallowed."""
        if self._running:
            return  # a slow handler is still running → drop this tick (no queue, no pile-up)
        self._running = True
        try:
            for name, handler in list(self._handlers.items()):
                try:
                    handler()
                except Exception:  # noqa: BLE001 — an ephemeral tick must never crash the UI
                    _log.exception("tick handler %r failed", name)
        finally:
            self._running = False


def _quiet_vetoed(now: datetime, entry: ScheduleEntry, quiet_hours: tuple[int, int] | None) -> bool:
    """Quiet hours suppress every trigger **except** an explicit ``at:`` the owner set (an alarm beats
    quiet hours; a periodic glance doesn't)."""
    if not quiet_hours or not _in_quiet_hours(now, quiet_hours):
        return False
    return entry.trigger.kind != "at"


def _at_matches(entry: ScheduleEntry, minute: datetime) -> bool:
    """Does a fixed-time (``at``/``cron``) entry's raw pattern match this minute (ignoring last-fired)?"""
    t = entry.trigger
    if t.kind == "at":
        if t.at_hm is None or (minute.hour, minute.minute) != t.at_hm:
            return False
        return not t.days or minute.weekday() in t.days
    if t.kind == "cron":
        return _cron_match(t.cron, minute)
    return False


class Scheduler:
    """Holds the schedule + last-fired state and decides which entries fire on a tick.

    ``day_cap`` is the global daily cap; ``per_dir_cap`` the per-directive daily cap (both restraint,
    counted in memory and reset at local midnight — a restart resets them, which is fine: the catch-up
    cap handles downtime). ``quiet_hours`` vetoes non-``at`` triggers inside the window.
    """

    def __init__(
        self,
        entries: list[ScheduleEntry],
        state_path: str | Path,
        *,
        day_cap: int = 24,
        per_dir_cap: int | None = None,
        quiet_hours: tuple[int, int] | None = None,
    ) -> None:
        self._entries = [e for e in entries if e.enabled]
        self._state_path = Path(state_path)
        self._state: dict[str, str] = load_state(state_path)
        self._day_cap = max(0, day_cap)
        self._per_dir_cap = self._day_cap if per_dir_cap is None else max(0, per_dir_cap)
        self._quiet_hours = quiet_hours
        self._day: str | None = None
        self._day_count = 0
        self._per_dir: dict[str, int] = {}

    # --- day-cap bookkeeping (in-memory) ----------------------------------------------------------
    def _roll_day(self, now: datetime) -> None:
        today = now.date().isoformat()
        if today != self._day:
            self._day, self._day_count, self._per_dir = today, 0, {}

    def _capped(self, entry: ScheduleEntry) -> bool:
        return (self._day_count >= self._day_cap
                or self._per_dir.get(entry.directive, 0) >= self._per_dir_cap)

    def _stamp(self, entry: ScheduleEntry, now: datetime) -> None:
        self._state[entry.id] = now.isoformat()
        self._day_count += 1
        self._per_dir[entry.directive] = self._per_dir.get(entry.directive, 0) + 1
        save_state(self._state_path, self._state)

    # --- the tick ---------------------------------------------------------------------------------
    def due_now(self, now: datetime, *, last_input: datetime | None = None) -> list[ScheduleEntry]:
        """The entries to fire at ``now`` — evaluates :func:`due`, vetoes quiet hours, honours the caps,
        and **stamps** each fired entry (last-fired + counts). Pure of Textual; safe to call each tick."""
        self._roll_day(now)
        fires: list[ScheduleEntry] = []
        for entry in self._entries:
            if not due(now, last_fired_of(self._state, entry), entry.trigger, last_input=last_input):
                continue
            if _quiet_vetoed(now, entry, self._quiet_hours):
                continue
            if self._capped(entry):
                _log.debug("scheduler: %s capped for %s", entry.directive, self._day)
                continue
            self._stamp(entry, now)
            fires.append(entry)
        return fires

    # --- startup catch-up -------------------------------------------------------------------------
    def catch_up(self, now: datetime, *, catchup_h: int = 6) -> list[ScheduleEntry]:
        """Fire fixed-time (``at``/``cron``) entries whose most-recent scheduled minute within the last
        ``catchup_h`` was **missed** while the TUI was closed. Only the most-recent occurrence fires
        (never a backlog); older-than-window is skipped. Live triggers (``every``/``idle``/``between``)
        are handled by the normal tick, so they're excluded here."""
        self._roll_day(now)
        fires: list[ScheduleEntry] = []
        window_start = now - timedelta(hours=max(0, catchup_h))
        for entry in self._entries:
            if entry.trigger.kind not in ("at", "cron"):
                continue
            last = last_fired_of(self._state, entry)
            occ = self._most_recent_occurrence(entry, now, window_start)
            if occ is None or (last is not None and occ <= last):
                continue  # nothing due in the window, or already fired at/after it
            if _quiet_vetoed(now, entry, self._quiet_hours) or self._capped(entry):
                continue
            self._stamp(entry, now)  # fire once, now (stamped so the live tick won't re-fire this minute)
            fires.append(entry)
        return fires

    @staticmethod
    def _most_recent_occurrence(
        entry: ScheduleEntry, now: datetime, window_start: datetime
    ) -> datetime | None:
        """Scan minutes back from ``now`` to ``window_start``; the most-recent matching minute, or None."""
        minute = now.replace(second=0, microsecond=0)
        while minute >= window_start:
            if _at_matches(entry, minute):
                return minute
            minute -= timedelta(minutes=1)
        return None

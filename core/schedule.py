"""Thought scheduler — the trigger model (v0.42 LUMI-166).

The pure, clock-driven foundation for the in-TUI scheduler (the loop itself lives in the TUI, v0.42
LUMI-167). A **schedule entry** binds one **trigger** to one **directive (+ a raw seed)**; each trigger
reduces to a pure :func:`due` ``(now, last_fired, trigger) -> bool`` predicate — **no sleeps**, so it is
unit-testable with a fixed clock, exactly like :func:`core.nudge.should_nudge`.

Five trigger types (ascending in specificity):

- ``every: <dur>``    — a wall-clock interval since the last fire.
- ``idle: <dur>``     — idle since the last real input (the migrated v0.4/v0.12 nudge rule).
- ``at: <HH:MM>``     — a fixed daily/weekly minute (fires **once** at its minute), optional ``days``.
- ``between: <HH:MM-HH:MM>, every: <dur>`` — a windowed periodic.
- ``cron: <expr>``    — a raw 5-field cron (minute hour dom month dow).

Placeholder topics (``{ambient_news}`` …) are kept **raw** here; they resolve at fire time in the TUI
(the v0.12 ``resolve``), never in this module. No core change, no model call — this is scheduling math.
See features/THOUGHT_SCHEDULER.md.
"""

from __future__ import annotations

import json
import logging
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_log = logging.getLogger("lumi.schedule")

# Day-of-week names → Python weekday (0 = Monday … 6 = Sunday).
DOW: dict[str, int] = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}

_TRIGGER_KEYS = ("cron", "between", "at", "every", "idle")  # detection order (most specific first)


def _parse_duration(raw: object) -> int:
    """A duration like ``"10m"`` / ``"2h"`` / ``"600s"`` / ``"90"`` → **seconds** (0 on junk)."""
    s = str(raw or "").strip().lower()
    if not s:
        return 0
    unit, mult = s[-1], 1
    if unit == "s":
        s, mult = s[:-1], 1
    elif unit == "m":
        s, mult = s[:-1], 60
    elif unit == "h":
        s, mult = s[:-1], 3600
    elif unit == "d":
        s, mult = s[:-1], 86400
    try:
        return max(0, int(float(s) * mult))
    except ValueError:
        return 0


def _parse_hm(raw: object) -> tuple[int, int] | None:
    """``"08:00"`` → ``(8, 0)``; anything else → ``None``."""
    s = str(raw or "").strip()
    if ":" not in s:
        return None
    try:
        h, m = (int(x) for x in s.split(":", 1))
    except ValueError:
        return None
    if 0 <= h < 24 and 0 <= m < 60:
        return (h, m)
    return None


@dataclass(frozen=True)
class Trigger:
    """One parsed trigger — the ``kind`` selects which fields matter (see :func:`due`)."""

    kind: str                                  # every | idle | at | between | cron
    interval_s: int = 0                        # every / idle / between
    at_hm: tuple[int, int] | None = None       # at
    days: tuple[int, ...] = ()                 # at — restrict to these weekdays (empty = every day)
    window: tuple[tuple[int, int], tuple[int, int]] | None = None  # between — (start_hm, end_hm)
    cron: str = ""                             # cron — the raw 5-field expression

    def canonical(self) -> str:
        """A stable string for id/state keying (order-independent within a kind)."""
        return f"{self.kind}|{self.interval_s}|{self.at_hm}|{self.days}|{self.window}|{self.cron}"


@dataclass(frozen=True)
class ScheduleEntry:
    """One authored schedule row: a directive + a trigger + an optional **raw** seed.

    ``seeds`` (v0.42) is an alternative to ``directive``/``topic``: a path to a file of ``%directive``
    lines (e.g. ``core/think_seeds.md``) — the fire picks one at **random** (no immediate repeat), so a
    single row carries a whole seed menu (the migrated in-app ``%think`` A-menu). When ``seeds`` is set,
    ``directive`` is a nominal label for the id/cap bucket (defaults to ``"seeds"``).
    """

    directive: str
    trigger: Trigger
    topic: str | None = None
    enabled: bool = True
    seeds: str = ""  # a %directive-lines file; the fire picks one at random
    days_raw: tuple[str, ...] = field(default=(), repr=False)  # for round-trip/debug only

    @property
    def id(self) -> str:
        """A content-stable id (survives reordering) for the ``schedule.state`` key."""
        return f"{self.directive}#{self.trigger.canonical()}#{self.topic or ''}#{self.seeds}"


# --- the pure predicate ----------------------------------------------------------------------------
def _floor_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


def _new_minute(last_fired: datetime | None, now: datetime) -> bool:
    """True if ``now`` is a later minute than ``last_fired`` — the once-per-minute guard for ``at``/``cron``
    so repeated sub-minute ticks never double-fire."""
    return last_fired is None or _floor_minute(last_fired) < _floor_minute(now)


def _in_window(now: datetime, window: tuple[tuple[int, int], tuple[int, int]]) -> bool:
    (sh, sm), (eh, em) = window
    cur = now.hour * 60 + now.minute
    start, end = sh * 60 + sm, eh * 60 + em
    if start <= end:
        return start <= cur < end
    return cur >= start or cur < end  # wraps past midnight


def due(
    now: datetime,
    last_fired: datetime | None,
    trigger: Trigger,
    *,
    last_input: datetime | None = None,
) -> bool:
    """Is this trigger due at ``now``? Pure — no sleeps, no quiet-hours/caps (those live in the loop).

    ``last_fired`` is this entry's previous fire (``None`` = never); ``last_input`` is the TUI's own
    in-memory last real input, required by ``idle:`` (ignored otherwise).
    """
    k = trigger.kind
    if k == "every":
        return last_fired is None or (now - last_fired).total_seconds() >= trigger.interval_s
    if k == "idle":
        anchor = last_input or now
        if last_fired is not None:
            anchor = max(anchor, last_fired)
        return (now - anchor).total_seconds() >= trigger.interval_s
    if k == "at":
        if trigger.at_hm is None:
            return False
        h, m = trigger.at_hm
        if now.hour != h or now.minute != m:
            return False
        if trigger.days and now.weekday() not in trigger.days:
            return False
        return _new_minute(last_fired, now)
    if k == "between":
        if trigger.window is None or not _in_window(now, trigger.window):
            return False
        return last_fired is None or (now - last_fired).total_seconds() >= trigger.interval_s
    if k == "cron":
        return _cron_match(trigger.cron, now) and _new_minute(last_fired, now)
    return False


# --- cron (5-field: minute hour day-of-month month day-of-week) ------------------------------------
def _cron_field(field_str: str, lo: int, hi: int) -> set[int]:
    """Expand one cron field (``*`` / ``*/n`` / ``a-b`` / ``a,b`` / ``a-b/n`` / ``v``) to its value set."""
    out: set[int] = set()
    for part in field_str.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            step = max(1, int(step_s))
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
        else:
            a = b = int(part)
        out.update(range(a, b + 1, step))
    return out


def _cron_match(expr: str, now: datetime) -> bool:
    """True if ``now`` (to the minute) matches a 5-field cron ``expr`` (standard dom/dow OR rule)."""
    parts = expr.split()
    if len(parts) != 5:
        return False
    minute, hour, dom, month, dow = parts
    try:
        if now.minute not in _cron_field(minute, 0, 59):
            return False
        if now.hour not in _cron_field(hour, 0, 23):
            return False
        if now.month not in _cron_field(month, 1, 12):
            return False
        dom_set = _cron_field(dom, 1, 31)
        dow_set = _cron_field(dow, 0, 7)  # cron dow: 0 or 7 = Sunday
        dow_now = (now.weekday() + 1) % 7  # Python Mon=0 → cron Sun=0
        dom_ok = now.day in dom_set
        dow_ok = dow_now in dow_set or (dow_now == 0 and 7 in dow_set)
        dom_restricted, dow_restricted = dom.strip() != "*", dow.strip() != "*"
        if dom_restricted and dow_restricted:
            return dom_ok or dow_ok  # the classic cron OR when BOTH are restricted
        return dom_ok and dow_ok
    except (ValueError, TypeError):
        return False


# --- parsing core/schedule.toml --------------------------------------------------------------------
def _entry_from_row(row: dict) -> ScheduleEntry | None:
    """One ``[[schedule]]`` row → a :class:`ScheduleEntry`, or ``None`` if malformed (skipped)."""
    directive = str(row.get("directive", "")).strip()
    seeds = str(row.get("seeds", "")).strip()
    if not directive and not seeds:
        return None
    if not directive:  # a seeds-menu row: a nominal label for the id/cap bucket
        directive = "seeds"
    topic = row.get("topic")
    topic = str(topic).strip() if topic is not None else None
    enabled = bool(row.get("enabled", True))
    days_raw = tuple(str(d).strip().lower() for d in row.get("days", []) if str(d).strip())
    days = tuple(DOW[d] for d in days_raw if d in DOW)

    trig: Trigger | None = None
    if "cron" in row:
        expr = str(row["cron"]).strip()
        trig = Trigger("cron", cron=expr) if len(expr.split()) == 5 else None
    elif "between" in row:
        span = str(row["between"]).strip()
        every = _parse_duration(row.get("every"))
        if "-" in span and every > 0:
            a, b = span.split("-", 1)
            sa, sb = _parse_hm(a), _parse_hm(b)
            if sa and sb:
                trig = Trigger("between", interval_s=every, window=(sa, sb))
    elif "at" in row:
        hm = _parse_hm(row["at"])
        if hm:
            trig = Trigger("at", at_hm=hm, days=days)
    elif "every" in row:
        secs = _parse_duration(row["every"])
        if secs > 0:
            trig = Trigger("every", interval_s=secs)
    elif "idle" in row:
        secs = _parse_duration(row["idle"])
        if secs > 0:
            trig = Trigger("idle", interval_s=secs)

    if trig is None:
        return None
    return ScheduleEntry(
        directive=directive, trigger=trig, topic=topic, enabled=enabled, seeds=seeds, days_raw=days_raw,
    )


def parse_schedule(text: str) -> list[ScheduleEntry]:
    """Parse ``schedule.toml`` text → entries. Malformed rows are skipped (never fatal)."""
    try:
        data = tomllib.loads(text)
    except (ValueError, TypeError) as exc:
        _log.warning("schedule.toml parse failed: %s", exc)
        return []
    out: list[ScheduleEntry] = []
    for row in data.get("schedule", []):
        if not isinstance(row, dict):
            continue
        entry = _entry_from_row(row)
        if entry is not None:
            out.append(entry)
    return out


def load_schedule(path: str | Path) -> list[ScheduleEntry]:
    """Read + parse the authored schedule file. A missing/unreadable file → ``[]`` (never raises)."""
    p = Path(path)
    try:
        return parse_schedule(p.read_text(encoding="utf-8"))
    except OSError:
        return []


# --- schedule.state (last-fired per entry) ---------------------------------------------------------
def load_state(path: str | Path) -> dict[str, str]:
    """Read ``schedule.state`` → ``{entry_id: last_fired_iso}``. Missing/corrupt → ``{}`` (never raises)."""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def save_state(path: str | Path, state: dict[str, str]) -> None:
    """Write ``{entry_id: last_fired_iso}`` to ``schedule.state`` (best-effort; logs on failure)."""
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        _log.warning("schedule.state write failed: %s", exc)


def last_fired_of(state: dict[str, str], entry: ScheduleEntry) -> datetime | None:
    """The stored last-fired stamp for ``entry`` (parsed to a datetime), or ``None``."""
    raw = state.get(entry.id)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None

"""v4.4 LUMI-315 — when each directive is next due (``core/schedule.py`` ``next_due`` / ``trigger_view``,
``core/scheduler.py`` ``Scheduler.view``).

Pinned: each trigger kind by the scheduler's own ``due``; quiet hours stop every kind but ``at``; a reached day cap
(global or the directive's) moves the next due past midnight; a disabled entry is listed with none; ``last_fired``
is ``schedule.state``'s; the view changes neither the state file nor the counts. Pure — an injected ``now``."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta, timezone

import pytest

from core.schedule import ScheduleEntry, Trigger, due, next_due, save_state, trigger_view
from core.scheduler import Scheduler

T = datetime(2026, 11, 18, 10, 0)  # a Wednesday


def at(h, m, days=()):
    return Trigger("at", at_hm=(h, m), days=days)


EVERY_30 = Trigger("every", interval_s=1800)
IDLE_15 = Trigger("idle", interval_s=900)
MORNING = Trigger("between", interval_s=1200, window=((7, 0), (9, 0)))
NIGHT = Trigger("between", interval_s=3600, window=((22, 0), (2, 0)))
CRON = Trigger("cron", cron="*/10 7-9 * * *")


def hm(day_offset, h, m, s=0):
    return (T + timedelta(days=day_offset)).replace(hour=h, minute=m, second=s)


# --- next_due, kind by kind ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(("trigger", "now", "last", "last_input", "expected"), [
    (EVERY_30, hm(0, 10, 10), None, None, hm(0, 10, 10)),  # never fired → now
    (EVERY_30, hm(0, 10, 10), hm(0, 10, 0), None, hm(0, 10, 30)),
    (IDLE_15, hm(0, 10, 5), None, hm(0, 10, 0), hm(0, 10, 15)),
    (IDLE_15, hm(0, 10, 21), hm(0, 10, 20), hm(0, 10, 0), hm(0, 10, 35)),  # the later of input and fire
    (IDLE_15, hm(0, 10, 5), None, None, hm(0, 10, 20)),  # no input yet: from now
    (at(8, 0), hm(0, 7, 0), None, None, hm(0, 8, 0)),
    (at(8, 0), hm(0, 9, 0), None, None, hm(1, 8, 0)),
    (at(8, 0), hm(0, 8, 0, 30), hm(0, 8, 0, 5), None, hm(1, 8, 0)),  # fired this minute → tomorrow
    (at(8, 0), hm(0, 8, 0, 30), hm(-1, 8, 0), None, hm(0, 8, 0, 30)),  # inside its minute, not fired → now
    (at(8, 0, days=(5,)), hm(0, 7, 0), None, None, hm(3, 8, 0)),  # Saturdays only: Wed → Sat
    (MORNING, hm(0, 6, 0), None, None, hm(0, 7, 0)),
    (MORNING, hm(0, 7, 30), hm(0, 7, 20), None, hm(0, 7, 40)),
    (MORNING, hm(0, 8, 59), hm(0, 8, 50), None, hm(1, 7, 0)),  # 09:10 is past the window
    (NIGHT, hm(0, 1, 30), hm(0, 1, 0), None, hm(0, 22, 0)),  # 02:00 ends the wrapped window
    (NIGHT, hm(0, 23, 30), hm(0, 23, 0), None, hm(1, 0, 0)),  # across midnight inside the window
    (CRON, hm(0, 6, 55), None, None, hm(0, 7, 0)),
    (CRON, hm(0, 7, 5), hm(0, 7, 0), None, hm(0, 7, 10)),
])
def test_next_due_by_kind(trigger, now, last, last_input, expected):
    got = next_due(now, last, trigger, last_input=last_input)
    assert got == expected
    assert due(got, last, trigger, last_input=last_input or now)  # the scheduler would fire then


def test_quiet_hours_stop_every_kind_but_at():
    quiet = (1, 7)
    assert next_due(hm(0, 3, 0), None, EVERY_30, quiet_hours=quiet) == hm(0, 7, 0)
    assert next_due(hm(0, 0, 50), None, IDLE_15, last_input=hm(0, 0, 50), quiet_hours=quiet) == hm(0, 7, 0)
    assert next_due(hm(0, 0, 50), None, CRON, quiet_hours=(1, 8)) == hm(0, 8, 0)
    assert next_due(hm(0, 3, 0), None, at(5, 0), quiet_hours=quiet) == hm(0, 5, 0)  # an alarm beats quiet hours
    assert next_due(hm(0, 3, 0), None, EVERY_30, quiet_hours=(3, 3)) == hm(0, 3, 0)  # an empty window vetoes nothing


def test_none_past_the_horizon():
    assert next_due(hm(0, 9, 0), None, at(8, 0), horizon_days=0) is None
    assert next_due(hm(0, 9, 0), None, Trigger("cron", cron="0 3 31 2 *")) is None  # 31 February never comes


def test_aware_times_keep_their_zone():
    kyiv = timezone(timedelta(hours=2))
    now = datetime(2026, 11, 18, 10, 0, tzinfo=kyiv)
    last = datetime(2026, 11, 18, 7, 50, tzinfo=UTC)  # 09:50 in Kyiv
    assert next_due(now, last, EVERY_30) == datetime(2026, 11, 18, 10, 20, tzinfo=kyiv)


def test_trigger_view():
    assert trigger_view(at(8, 0, days=(0, 4))) == {"kind": "at", "interval_s": None, "at": "08:00", "days": [0, 4],
                                                   "window": None, "cron": None}
    assert trigger_view(MORNING) == {"kind": "between", "interval_s": 1200, "at": None, "days": [],
                                     "window": ["07:00", "09:00"], "cron": None}
    assert trigger_view(IDLE_15)["interval_s"] == 900 and trigger_view(CRON)["cron"] == "*/10 7-9 * * *"


# --- Scheduler.view -----------------------------------------------------------------------------------------------
def _entries():
    return [ScheduleEntry("think", IDLE_15, seeds="core/think_seeds.md", name="muse-silent"),
            ScheduleEntry("brief", at(8, 0), name="morning-brief"),
            ScheduleEntry("catchup", MORNING, name="catchup", show=True),
            ScheduleEntry("learn", at(22, 30), name="learn-night", enabled=False)]


def _scheduler(tmp_path, state=None, **kw):
    path = tmp_path / "schedule.state"
    if state is not None:
        save_state(path, state)
    return Scheduler(_entries(), path, quiet_hours=(1, 7), **kw), path


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def test_the_view_lists_every_entry_with_its_last_and_next(tmp_path):
    entries = _entries()
    state = {entries[1].id: hm(0, 8, 0, 3).isoformat(), entries[3].id: hm(-1, 22, 30).isoformat()}
    sched, path = _scheduler(tmp_path, state)
    before = _digest(path)
    view = sched.view(hm(0, 10, 0), last_input=hm(0, 9, 50))
    assert view["now"] == "2026-11-18T10:00" and view["quiet_hours"] == [1, 7]
    assert view["day_cap"] == 24 and view["fired_today"] == 0
    rows = {e["name"]: e for e in view["entries"]}
    assert list(rows) == ["muse-silent", "morning-brief", "catchup", "learn-night"]  # the schedule's order
    assert rows["muse-silent"] == {"name": "muse-silent", "directive": "think", "seeds": "core/think_seeds.md",
                                   "show": False, "enabled": True, "trigger": trigger_view(IDLE_15),
                                   "last_fired": None, "next_due": "2026-11-18T10:05", "if_silent": True}
    assert rows["morning-brief"]["last_fired"] == "2026-11-18T08:00"
    assert rows["morning-brief"]["next_due"] == "2026-11-19T08:00" and not rows["morning-brief"]["if_silent"]
    assert rows["catchup"]["next_due"] == "2026-11-19T07:00" and rows["catchup"]["show"] is True
    assert rows["learn-night"]["enabled"] is False and rows["learn-night"]["next_due"] is None
    assert rows["learn-night"]["last_fired"] == "2026-11-17T22:30"  # disabled, still listed with its past
    assert _digest(path) == before


def test_a_reached_cap_moves_the_next_due_past_midnight(tmp_path):
    sched, _ = _scheduler(tmp_path, day_cap=3)
    sched._roll_day(hm(0, 9, 0))
    sched._day_count = 3  # three fired today: the day cap
    view = sched.view(hm(0, 10, 0), last_input=hm(0, 9, 50))
    rows = {e["name"]: e for e in view["entries"]}
    assert view["fired_today"] == 3
    assert rows["muse-silent"]["next_due"] == "2026-11-19T00:00"  # right after midnight (quiet starts at 01:00)
    assert rows["morning-brief"]["next_due"] == "2026-11-19T08:00"


def test_a_directives_own_cap_moves_only_that_directive(tmp_path):
    sched, _ = _scheduler(tmp_path, day_cap=10, per_dir_cap=1)
    sched._roll_day(hm(0, 9, 0))
    sched._day_count, sched._per_dir = 1, {"think": 1}
    rows = {e["name"]: e for e in sched.view(hm(0, 10, 0), last_input=hm(0, 9, 50))["entries"]}
    assert rows["muse-silent"]["next_due"] == "2026-11-19T00:00"
    assert rows["catchup"]["next_due"] == "2026-11-19T07:00"  # its own window, not a cap
    assert sched._per_dir == {"think": 1} and sched._day_count == 1  # untouched


def test_yesterdays_counts_are_not_today_s(tmp_path):
    sched, _ = _scheduler(tmp_path, day_cap=3)
    sched._roll_day(hm(-1, 23, 0))
    sched._day_count = 3
    view = sched.view(hm(0, 10, 0), last_input=hm(0, 9, 50))
    assert view["fired_today"] == 0
    assert {e["name"]: e for e in view["entries"]}["muse-silent"]["next_due"] == "2026-11-18T10:05"
    assert sched._day == "2026-11-17"  # the view never rolls the day

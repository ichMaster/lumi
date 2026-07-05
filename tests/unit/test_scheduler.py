"""v0.42 LUMI-167 — the in-TUI scheduler: due-planning, quiet-hours veto, caps, catch-up + the
run_directive seam. Pure of Textual (no timers, no sleeps); the model is mocked."""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from core.schedule import ScheduleEntry, Trigger, load_state
from state.local_store import JsonRepository
from tui.scheduler import Scheduler


def _dt(y=2026, mo=7, d=6, h=8, mi=0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def _sched(tmp_path, entries, **kw) -> Scheduler:
    return Scheduler(entries, tmp_path / "schedule.state", **kw)


# --- due_now: firing + stamping --------------------------------------------------------------------
def test_due_now_fires_enabled_and_stamps(tmp_path):
    e = ScheduleEntry("think", Trigger("every", interval_s=600))
    s = _sched(tmp_path, [e])
    fired = s.due_now(_dt(h=8, mi=0))
    assert fired == [e]  # first tick → due
    assert s.due_now(_dt(h=8, mi=5)) == []  # 5m < 10m since the stamp → not due
    assert s.due_now(_dt(h=8, mi=10)) == [e]  # 10m later → due again
    assert load_state(tmp_path / "schedule.state")[e.id]  # last-fired persisted


def test_disabled_entries_never_fire(tmp_path):
    e = ScheduleEntry("think", Trigger("every", interval_s=1), enabled=False)
    assert _sched(tmp_path, [e]).due_now(_dt()) == []


def test_idle_uses_last_input(tmp_path):
    e = ScheduleEntry("think", Trigger("idle", interval_s=600))
    s = _sched(tmp_path, [e])
    assert s.due_now(_dt(h=8, mi=5), last_input=_dt(h=8, mi=0)) == []  # 5m idle < 10m
    assert s.due_now(_dt(h=8, mi=10), last_input=_dt(h=8, mi=0)) == [e]  # 10m idle → due


# --- quiet-hours veto ------------------------------------------------------------------------------
def test_quiet_hours_veto_spares_explicit_at(tmp_path):
    every = ScheduleEntry("catchup", Trigger("every", interval_s=1))
    alarm = ScheduleEntry("brief", Trigger("at", at_hm=(2, 0)))  # inside 23-7
    s = _sched(tmp_path, [every, alarm], quiet_hours=(23, 7))
    fired = s.due_now(_dt(h=2, mi=0))
    assert every not in fired  # a periodic glance is vetoed in quiet hours
    assert alarm in fired  # a deliberate at: pierces quiet hours


# --- caps ------------------------------------------------------------------------------------------
def test_global_day_cap_suppresses(tmp_path):
    e = ScheduleEntry("think", Trigger("every", interval_s=0))  # due every tick
    s = _sched(tmp_path, [e], day_cap=2)
    assert s.due_now(_dt(h=8, mi=0)) == [e]
    assert s.due_now(_dt(h=8, mi=1)) == [e]
    assert s.due_now(_dt(h=8, mi=2)) == []  # cap 2 reached for the day
    assert s.due_now(_dt(d=7, h=8, mi=0)) == [e]  # next day → the cap resets


def test_per_directive_cap(tmp_path):
    a = ScheduleEntry("catchup", Trigger("every", interval_s=0))
    b = ScheduleEntry("brief", Trigger("every", interval_s=0))
    s = _sched(tmp_path, [a, b], day_cap=10, per_dir_cap=1)
    fired = s.due_now(_dt(h=8, mi=0))
    assert a in fired and b in fired  # each directive's first fire is allowed
    fired2 = s.due_now(_dt(h=8, mi=1))
    assert fired2 == []  # both directives hit their per-directive cap of 1


# --- catch-up --------------------------------------------------------------------------------------
def test_catch_up_fires_most_recent_missed_at(tmp_path):
    e = ScheduleEntry("brief", Trigger("at", at_hm=(8, 0)))
    s = _sched(tmp_path, [e])
    # started at 09:30; the 08:00 fire was missed within the 6h window → fire once
    fired = s.catch_up(_dt(h=9, mi=30), catchup_h=6)
    assert fired == [e]
    # already stamped → the live tick this minute won't re-fire
    assert s.due_now(_dt(h=9, mi=30)) == []


def test_catch_up_skips_stale_beyond_window(tmp_path):
    e = ScheduleEntry("brief", Trigger("at", at_hm=(8, 0)))
    s = _sched(tmp_path, [e])
    # started at 18:00; 08:00 is 10h back, beyond the 6h window → skip
    assert s.catch_up(_dt(h=18, mi=0), catchup_h=6) == []


def test_catch_up_excludes_live_triggers(tmp_path):
    idle = ScheduleEntry("think", Trigger("idle", interval_s=600))
    every = ScheduleEntry("catchup", Trigger("every", interval_s=600))
    s = _sched(tmp_path, [idle, every])
    assert s.catch_up(_dt(h=9, mi=0), catchup_h=6) == []  # idle/every handled by the live tick


# --- the run_directive seam (integration; mock model, injected clock) ------------------------------
def test_scheduled_entry_runs_through_run_directive_and_records_a_thought(tmp_path):
    clock = fixed_clock(_dt(h=8, mi=0))
    core = Core(
        llm=MockLLMClient(replies="маленька думка\nЕМОЦІЯ: joy"),
        repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m",
        clock=clock, mood_enabled=False, thoughts_enabled=True,
    )
    session = core.start_session()
    e = ScheduleEntry("think", Trigger("every", interval_s=600))
    s = _sched(tmp_path, [e])
    for entry in s.due_now(clock()):
        text = f"%{entry.directive}" + (f" {entry.topic}" if entry.topic else "")
        outcome = core.run_directive(text, session)
        assert outcome.is_directive and outcome.thought is not None
    assert core.recent_thoughts()  # the scheduled fire recorded a Thought (silent surfacing)


# --- LUMI-168: the fast tick service (ephemeral code handlers, not model directives) ---------------
def test_tick_service_runs_registered_handlers():
    from tui.scheduler import TickService

    svc = TickService()
    calls = []
    svc.register("update_state", lambda: calls.append("x"))
    svc.tick()
    svc.tick()
    assert calls == ["x", "x"]  # a registered code handler runs each tick — no Thought, no model call


def test_tick_service_swallows_a_raising_handler():
    from tui.scheduler import TickService

    svc = TickService()
    ok = []
    svc.register("boom", lambda: (_ for _ in ()).throw(RuntimeError("nope")))
    svc.register("fine", lambda: ok.append(1))
    svc.tick()  # must not raise
    assert ok == [1]  # a sibling handler still runs after one raises


def test_tick_service_collapses_reentrant_tick():
    from tui.scheduler import TickService

    svc = TickService()
    seen = []

    def slow():
        seen.append("start")
        svc.tick()  # a re-entrant tick while this one runs → dropped (no pile-up)
        seen.append("end")

    svc.register("slow", slow)
    svc.tick()
    assert seen == ["start", "end"]  # the nested tick did NOT re-run the handler

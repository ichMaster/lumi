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


# --- LUMI-169: migrate idle triggers → idle: entries; retire the in-app timers ---------------------
def test_graduates_only_muse_family_and_respects_ratio():
    from tui.scheduler import graduates

    assert graduates("think", 0, 1.0) is True  # ratio 1 → always
    assert graduates("wonder", 0, 1.0) is True
    assert graduates("think", 0, 0.0) is False  # ratio 0 → never
    assert graduates("brief", 0, 1.0) is False  # not an idle-muse directive → never speaks
    assert graduates("catchup", 0, 1.0) is False
    # deterministic per seed (should_graduate: seed % 100 < ratio*100)
    assert graduates("think", 10, 0.2) is True and graduates("think", 30, 0.2) is False


def test_migrated_schedule_has_idle_seeds_row():
    # Structural — core/schedule.toml is user-editable, so assert the migration SHAPE (an idle seeds
    # menu), not the enabled-state (the owner may enable rituals / tune rows).
    from core.config import DEFAULT_SCHEDULE_PATH
    from core.schedule import load_schedule

    entries = load_schedule(DEFAULT_SCHEDULE_PATH)
    assert any(e.trigger.kind == "idle" and e.seeds for e in entries)  # the migrated idle muse


async def _mount(tmp_path, monkeypatch, *, scheduler: str):
    from tui.app import LumiApp

    monkeypatch.setenv("LUMI_SCHEDULER", scheduler)
    monkeypatch.setenv("LUMI_SCHED_TICK_MS", "600000")  # a long tick so nothing fires during the test
    monkeypatch.setenv("LUMI_SCHEDULE_STATE_PATH", str(tmp_path / "schedule.state"))
    core = Core(
        llm=MockLLMClient(states={"reply": "ок", "emotion": "calm", "intensity": 0.5}),
        repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m",
        clock=fixed_clock(_dt(h=14, mi=30)),  # far from the 08:00/23:00 at-entries → no catch-up fires
        mood_enabled=False, thoughts_enabled=True,
    )
    return LumiApp(core)


async def test_scheduler_on_wires_the_module(tmp_path, monkeypatch):
    app = await _mount(tmp_path, monkeypatch, scheduler="on")
    async with app.run_test():
        assert app._scheduler is not None and app._tick_service is not None  # scheduler owns the clock


async def test_scheduler_off_leaves_the_legacy_path(tmp_path, monkeypatch):
    app = await _mount(tmp_path, monkeypatch, scheduler="off")
    async with app.run_test():
        assert app._scheduler is None and app._tick_service is None  # legacy in-app timers own idle


def test_scheduled_text_picks_a_random_seed_line(tmp_path):
    from tui.app import LumiApp

    seeds = tmp_path / "seeds.md"
    seeds.write_text("# a comment (skipped)\n%think про каву\n%wonder! як справи\n", encoding="utf-8")
    core = Core(llm=MockLLMClient(states={"reply": "ок", "emotion": "calm", "intensity": 0.5}), repository=JsonRepository(tmp_path / "s.json"),
                canon="C", model="m", clock=fixed_clock(_dt()), mood_enabled=False, thoughts_enabled=True)
    app = LumiApp(core)
    e = ScheduleEntry("seeds", Trigger("idle", interval_s=900), seeds=str(seeds))
    picks = {app._scheduled_text(e) for _ in range(20)}
    assert picks <= {"%think про каву", "%wonder! як справи"}  # only the two %directive lines
    assert len(picks) == 2  # rotates over the menu (comment skipped)


def test_scheduled_text_plain_directive_row(tmp_path):
    from tui.app import LumiApp

    core = Core(llm=MockLLMClient(states={"reply": "ок", "emotion": "calm", "intensity": 0.5}), repository=JsonRepository(tmp_path / "s.json"),
                canon="C", model="m", clock=fixed_clock(_dt()), mood_enabled=False, thoughts_enabled=True)
    app = LumiApp(core)
    e = ScheduleEntry("brief", Trigger("at", at_hm=(8, 0)), topic="{interest}")
    assert app._scheduled_text(e) == "%brief {interest}"  # topic kept raw (resolves at fire time)


def test_scheduled_text_empty_seeds_file(tmp_path):
    from tui.app import LumiApp

    empty = tmp_path / "empty.md"
    empty.write_text("# only comments\n", encoding="utf-8")
    core = Core(llm=MockLLMClient(states={"reply": "ок", "emotion": "calm", "intensity": 0.5}), repository=JsonRepository(tmp_path / "s.json"),
                canon="C", model="m", clock=fixed_clock(_dt()), mood_enabled=False, thoughts_enabled=True)
    app = LumiApp(core)
    e = ScheduleEntry("seeds", Trigger("idle", interval_s=900), seeds=str(empty))
    assert app._scheduled_text(e) == ""  # nothing to fire → skipped by _run_scheduled


# --- v0.42: `show` writes the thought to the chat (like a typed %catchup!) --------------------------
async def test_show_row_emits_thought_to_chat(tmp_path, monkeypatch):
    from tui.app import LumiApp

    monkeypatch.setenv("LUMI_SCHEDULER", "on")
    monkeypatch.setenv("LUMI_SCHED_TICK_MS", "600000")  # long tick — we drive _run_scheduled directly
    monkeypatch.setenv("LUMI_SCHEDULE_STATE_PATH", str(tmp_path / "schedule.state"))
    monkeypatch.setenv("LUMI_THOUGHTS_SPOKEN_RATIO", "0")  # isolate `show` from graduation
    core = Core(
        llm=MockLLMClient(replies="у світі спокійно сьогодні\nЕМОЦІЯ: calm"),
        repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m",
        clock=fixed_clock(_dt(h=14, mi=30)), mood_enabled=False, thoughts_enabled=True,
    )
    app = LumiApp(core)
    async with app.run_test():
        shown = ScheduleEntry("think", Trigger("every", interval_s=1), show=True)
        await app._run_scheduled([shown])
        assert any("💭" in line for line in app.transcript)  # show=true → written to chat


async def test_silent_row_never_shows_or_speaks_even_at_max_ratio(tmp_path, monkeypatch):
    from tui.app import LumiApp

    monkeypatch.setenv("LUMI_SCHEDULER", "on")
    monkeypatch.setenv("LUMI_SCHED_TICK_MS", "600000")
    monkeypatch.setenv("LUMI_SCHEDULE_STATE_PATH", str(tmp_path / "schedule.state"))
    monkeypatch.setenv("LUMI_THOUGHTS_SPOKEN_RATIO", "1")  # even at 100%, a silent row must not speak
    core = Core(
        llm=MockLLMClient(replies="тиха думка\nЕМОЦІЯ: calm"),
        repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m",
        clock=fixed_clock(_dt(h=14, mi=30)), mood_enabled=False, thoughts_enabled=True,
    )
    app = LumiApp(core)
    async with app.run_test():
        silent = ScheduleEntry("think", Trigger("every", interval_s=1), show=False)  # not loud
        before = len(app.transcript)
        await app._run_scheduled([silent])
        # a silent row (show=false, no !) surfaces nothing — no 💭 line and no spoken turn — even at ratio 1
        assert not any("💭" in line for line in app.transcript)
        assert not any("тиха думка" in line for line in app.transcript[before:])  # no graduated reply
        assert core.recent_thoughts()  # but the Thought is still recorded (/thoughts + feedback)


async def test_loud_row_graduates_to_a_spoken_turn_at_max_ratio(tmp_path, monkeypatch):
    from tui.app import LumiApp

    monkeypatch.setenv("LUMI_SCHEDULER", "on")
    monkeypatch.setenv("LUMI_SCHED_TICK_MS", "600000")
    monkeypatch.setenv("LUMI_SCHEDULE_STATE_PATH", str(tmp_path / "schedule.state"))
    monkeypatch.setenv("LUMI_THOUGHTS_SPOKEN_RATIO", "1")  # a loud think graduates every time at 100%
    core = Core(
        llm=MockLLMClient(replies="я щойно подумала про течію\nЕМОЦІЯ: calm"),
        repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m",
        clock=fixed_clock(_dt(h=14, mi=30)), mood_enabled=False, thoughts_enabled=True,
    )
    app = LumiApp(core)
    async with app.run_test():
        loud = ScheduleEntry("think", Trigger("every", interval_s=1), show=True)  # loud
        await app._run_scheduled([loud])
        # graduation ran (a spoken self-turn) instead of the 💭 line
        assert any("течію" in line for line in app.transcript)  # her spoken reply reached the chat
        assert not any("💭" in line for line in app.transcript)  # graduated → not the 💭 branch


# --- v0.42: surface each scheduled execution in the chat (LUMI_THOUGHT_SURFACE) --------------------
async def test_scheduled_fire_logs_meta_line_when_surfacing_on(tmp_path, monkeypatch):
    from tui.app import LumiApp

    monkeypatch.setenv("LUMI_SCHEDULER", "on")
    monkeypatch.setenv("LUMI_SCHED_TICK_MS", "600000")
    monkeypatch.setenv("LUMI_SCHEDULE_STATE_PATH", str(tmp_path / "schedule.state"))
    monkeypatch.setenv("LUMI_THOUGHT_SURFACE", "on")  # the gate for the chat-log meta line
    monkeypatch.setenv("LUMI_THOUGHTS_SPOKEN_RATIO", "0")
    core = Core(
        llm=MockLLMClient(replies="тиха думка\nЕМОЦІЯ: calm"),
        repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m",
        clock=fixed_clock(_dt(h=14, mi=30)), mood_enabled=False, thoughts_enabled=True,
    )
    app = LumiApp(core)
    async with app.run_test():
        await app._run_scheduled([ScheduleEntry("think", Trigger("every", interval_s=1))])
        assert any(line.startswith("✦") for line in app.transcript)  # the act was marked in the chat


async def test_scheduled_fire_no_meta_line_when_surfacing_off(tmp_path, monkeypatch):
    from tui.app import LumiApp

    monkeypatch.setenv("LUMI_SCHEDULER", "on")
    monkeypatch.setenv("LUMI_SCHED_TICK_MS", "600000")
    monkeypatch.setenv("LUMI_SCHEDULE_STATE_PATH", str(tmp_path / "schedule.state"))
    monkeypatch.setenv("LUMI_THOUGHT_SURFACE", "off")  # default
    monkeypatch.setenv("LUMI_THOUGHTS_SPOKEN_RATIO", "0")
    core = Core(
        llm=MockLLMClient(replies="тиха думка\nЕМОЦІЯ: calm"),
        repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m",
        clock=fixed_clock(_dt(h=14, mi=30)), mood_enabled=False, thoughts_enabled=True,
    )
    app = LumiApp(core)
    async with app.run_test():
        await app._run_scheduled([ScheduleEntry("think", Trigger("every", interval_s=1))])
        assert not any(line.startswith("✦") for line in app.transcript)  # silent by default

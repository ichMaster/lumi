"""v0.42 LUMI-166 — the thought-scheduler trigger model (pure, fixed-clock, no sleeps)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.config import DEFAULT_SCHEDULE_PATH, load_config
from core.schedule import (
    ScheduleEntry,
    Trigger,
    due,
    last_fired_of,
    load_schedule,
    load_state,
    parse_schedule,
    save_state,
)


def _dt(y=2026, mo=7, d=6, h=8, mi=0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


# --- due(): every ----------------------------------------------------------------------------------
def test_every_fires_first_time_and_after_the_interval():
    t = Trigger("every", interval_s=600)
    now = _dt(h=8, mi=0)
    assert due(now, None, t)  # never fired → due
    assert not due(now, now, t)  # just fired → not due
    assert not due(now + timedelta(minutes=9), now, t)  # 9m < 10m
    assert due(now + timedelta(minutes=10), now, t)  # 10m elapsed → due


# --- due(): idle -----------------------------------------------------------------------------------
def test_idle_needs_silence_since_input_and_last_fire():
    t = Trigger("idle", interval_s=600)
    last_input = _dt(h=8, mi=0)
    assert not due(_dt(h=8, mi=5), None, t, last_input=last_input)  # 5m idle < 10m
    assert due(_dt(h=8, mi=10), None, t, last_input=last_input)  # 10m idle → due
    fired = _dt(h=8, mi=8)  # a recent fire pushes the anchor forward (independent pacing)
    assert not due(_dt(h=8, mi=15), fired, t, last_input=last_input)  # 7m since the fire < 10m


# --- due(): at -------------------------------------------------------------------------------------
def test_at_fires_once_at_its_minute():
    t = Trigger("at", at_hm=(8, 0))
    assert due(_dt(h=8, mi=0), None, t)  # at the minute, never fired → due
    assert not due(_dt(h=8, mi=1), None, t)  # a minute later → not the target
    fired = _dt(h=8, mi=0)
    assert not due(_dt(h=8, mi=0), fired, t)  # already fired this minute → guarded
    assert due(_dt(d=7, h=8, mi=0), fired, t)  # next day at 08:00 → due again


def test_at_respects_days():
    t = Trigger("at", at_hm=(8, 0), days=(0, 2, 4))  # mon/wed/fri
    assert _dt(2026, 7, 6).weekday() == 0  # 2026-07-06 is a Monday
    assert due(_dt(2026, 7, 6, 8, 0), None, t)  # Monday → due
    assert not due(_dt(2026, 7, 7, 8, 0), None, t)  # Tuesday → not in days


# --- due(): between --------------------------------------------------------------------------------
def test_between_windowed_periodic_no_double_fire():
    t = Trigger("between", interval_s=7200, window=((8, 0), (22, 0)))
    assert not due(_dt(h=7, mi=0), None, t)  # before the window
    assert due(_dt(h=8, mi=0), None, t)  # in window, never fired → due
    fired = _dt(h=8, mi=0)
    assert not due(_dt(h=9, mi=0), fired, t)  # 1h < 2h → not yet
    assert due(_dt(h=10, mi=0), fired, t)  # 2h elapsed, still in window → due
    assert not due(_dt(h=23, mi=0), fired, t)  # outside the window


# --- due(): cron -----------------------------------------------------------------------------------
def test_cron_every_10_min_weekday_morning():
    t = Trigger("cron", cron="*/10 7-9 * * 1-5")  # every 10m, 7-9am, Mon-Fri
    assert due(_dt(2026, 7, 6, 7, 10), None, t)  # Mon 07:10 → matches
    assert not due(_dt(2026, 7, 6, 7, 15), None, t)  # 07:15 → not a /10 minute
    assert not due(_dt(2026, 7, 6, 10, 0), None, t)  # 10:00 → outside 7-9
    assert not due(_dt(2026, 7, 5, 7, 10), None, t)  # Sunday → not Mon-Fri
    fired = _dt(2026, 7, 6, 7, 10)
    assert not due(_dt(2026, 7, 6, 7, 10, ), fired, t)  # same minute → guarded


def test_cron_sunday_zero_and_seven():
    for dow in ("0", "7"):
        t = Trigger("cron", cron=f"0 9 * * {dow}")
        assert due(_dt(2026, 7, 5, 9, 0), None, t)  # 2026-07-05 is a Sunday


# --- parser ----------------------------------------------------------------------------------------
_TOML = """
[[schedule]]
directive = "think"
idle = "10m"

[[schedule]]
directive = "catchup"
between = "08:00-22:00"
every   = "2h"
topic   = "{ambient_news}"

[[schedule]]
directive = "brief"
at    = "08:00"
days  = ["mon", "fri"]
topic = "{interest}"
enabled = false

[[schedule]]
directive = "prompt"
cron  = "*/10 7-9 * * 1-5"

[[schedule]]
directive = ""

[[schedule]]
at = "08:00"
"""


def test_parse_schedule_shapes_and_skips_malformed():
    entries = parse_schedule(_TOML)
    assert len(entries) == 4  # the empty-directive + directive-less rows are skipped
    by_dir = {e.directive: e for e in entries}
    assert by_dir["think"].trigger.kind == "idle" and by_dir["think"].trigger.interval_s == 600
    assert by_dir["catchup"].trigger.kind == "between"
    assert by_dir["catchup"].topic == "{ambient_news}"  # placeholder kept RAW
    assert by_dir["brief"].trigger.days == (0, 4) and by_dir["brief"].enabled is False
    assert by_dir["prompt"].trigger.kind == "cron"
    assert by_dir["think"].enabled is True  # default


def test_parse_malformed_toml_is_empty_never_raises():
    assert parse_schedule("not [ valid toml =") == []


def test_shipped_schedule_yaml_parses():
    # Structural only — core/schedule.yaml is user-editable runtime config, so don't pin enabled-state.
    entries = load_schedule(DEFAULT_SCHEDULE_PATH)
    assert len(entries) >= 5  # the idle seeds-menu + the ritual rows
    assert any(e.seeds and e.trigger.kind == "idle" for e in entries)  # the migrated idle seeds-menu


# --- schedule.state --------------------------------------------------------------------------------
def test_state_round_trips_last_fired(tmp_path):
    p = tmp_path / "schedule.state"
    entry = ScheduleEntry("think", Trigger("idle", interval_s=600))
    stamp = _dt(h=8, mi=0).isoformat()
    save_state(p, {entry.id: stamp})
    state = load_state(p)
    assert state[entry.id] == stamp
    assert last_fired_of(state, entry) == _dt(h=8, mi=0)
    assert last_fired_of({}, entry) is None  # nothing stored → None


def test_state_missing_or_corrupt_is_empty(tmp_path):
    assert load_state(tmp_path / "nope.state") == {}
    bad = tmp_path / "bad.state"
    bad.write_text("{ not json", encoding="utf-8")
    assert load_state(bad) == {}


def test_entry_id_is_content_stable():
    a = ScheduleEntry("brief", Trigger("at", at_hm=(8, 0)), topic="{interest}")
    b = ScheduleEntry("brief", Trigger("at", at_hm=(8, 0)), topic="{interest}")
    c = ScheduleEntry("brief", Trigger("at", at_hm=(9, 0)), topic="{interest}")
    assert a.id == b.id and a.id != c.id  # same content → same id; different trigger → different


# --- config ----------------------------------------------------------------------------------------
def test_config_scheduler_defaults(monkeypatch):
    for v in ("LUMI_SCHEDULER", "LUMI_SCHED_TICK_MS", "LUMI_SCHED_TICK_FAST_MS",
              "LUMI_SCHED_CATCHUP_H", "LUMI_SCHED_DAY_CAP", "LUMI_SCHEDULE_PATH"):
        monkeypatch.delenv(v, raising=False)
    cfg = load_config(load_env=False)
    assert cfg.scheduler is False  # off by default
    assert cfg.sched_tick_ms == 30000 and cfg.sched_tick_fast_ms == 60000
    assert cfg.sched_catchup_h == 6 and cfg.sched_day_cap == 24
    assert cfg.schedule_path == DEFAULT_SCHEDULE_PATH


def test_config_scheduler_reads_env(monkeypatch):
    monkeypatch.setenv("LUMI_SCHEDULER", "on")
    monkeypatch.setenv("LUMI_SCHED_TICK_MS", "15000")
    monkeypatch.setenv("LUMI_SCHED_DAY_CAP", "48")
    cfg = load_config(load_env=False)
    assert cfg.scheduler is True and cfg.sched_tick_ms == 15000 and cfg.sched_day_cap == 48


# --- v0.42: `seeds` rows (a %directive menu, one picked at random per fire) -------------------------
def test_parse_seeds_row_without_directive():
    entries = parse_schedule('[[schedule]]\nseeds = "core/think_seeds.md"\nidle = "15m"\n')
    assert len(entries) == 1
    e = entries[0]
    assert e.seeds == "core/think_seeds.md" and e.directive == "seeds"  # nominal label for the id/cap
    assert e.trigger.kind == "idle" and e.trigger.interval_s == 900


def test_seeds_row_id_is_stable_and_distinct():
    a = ScheduleEntry("seeds", Trigger("idle", interval_s=900), seeds="core/think_seeds.md")
    b = ScheduleEntry("seeds", Trigger("idle", interval_s=900), seeds="core/other.md")
    assert a.id != b.id  # the seeds path is part of the id


def test_shipped_idle_row_uses_the_seeds_menu():
    from core.config import DEFAULT_SCHEDULE_PATH
    idle = [e for e in load_schedule(DEFAULT_SCHEDULE_PATH)
            if e.trigger.kind == "idle" and e.enabled]
    assert idle and idle[0].seeds.endswith("think_seeds.md")  # the migrated idle muse reads the menu


# --- v0.42: `show` (write the thought to the chat, like a typed %name!) -----------------------------
def test_parse_show_flag():
    on = parse_schedule('[[schedule]]\ndirective = "catchup"\nevery = "2h"\nshow = true\n')[0]
    assert on.show is True
    off = parse_schedule('[[schedule]]\ndirective = "catchup"\nevery = "2h"\n')[0]
    assert off.show is False  # default silent


def test_shipped_catchup_row_shows_in_chat():
    from core.config import DEFAULT_SCHEDULE_PATH
    catchup = [e for e in load_schedule(DEFAULT_SCHEDULE_PATH) if e.directive == "catchup"]
    assert catchup and catchup[0].show is True  # the example demonstrates show = true


# --- v2.9 LUMI-246: core/schedule.yaml + an instance's rows by name -------------------------------------------

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "v2.9"


def test_the_yaml_schedule_loads_exactly_what_the_toml_did():
    from core.config import DEFAULT_SCHEDULE_PATH

    assert DEFAULT_SCHEDULE_PATH.name == "schedule.yaml"
    yaml_entries = load_schedule(DEFAULT_SCHEDULE_PATH)
    toml_entries = load_schedule(_FIXTURES / "schedule.toml")  # the pre-v2.9 file, kept as a fixture
    assert yaml_entries == toml_entries  # every field (the name is not part of equality)
    assert [e.id for e in yaml_entries] == [e.id for e in toml_entries]  # schedule.state keys unchanged
    assert all(e.name for e in yaml_entries)  # each authored row is named, so an instance can change it


def test_a_toml_path_still_loads_with_one_warning(caplog, tmp_path):
    p = tmp_path / "old.toml"
    p.write_text('[[schedule]]\ndirective = "think"\nidle = "10m"\n', encoding="utf-8")
    with caplog.at_level("WARNING", logger="lumi.schedule"):
        assert len(load_schedule(p)) == 1
        assert len(load_schedule(p)) == 1
    assert sum("deprecated since v2.9" in r.message for r in caplog.records) == 1


_AUTHORED = """schedule:
  - name: brief
    directive: brief
    at: "08:00"
    topic: science
    enabled: true
  - directive: think
    idle: "15m"
"""


def test_an_override_merges_over_the_row_of_the_same_name(tmp_path):
    from core.schedule import parse_schedule_yaml

    base = parse_schedule_yaml(_AUTHORED)
    off = parse_schedule_yaml(_AUTHORED, [{"name": "brief", "enabled": False}])
    assert [e.enabled for e in off] == [False, True]
    assert [e.id for e in off] == [e.id for e in base]  # turning a row off never changes its identity
    assert off[0].topic == "science" and off[0].trigger.kind == "at"  # the other fields stay


def test_an_override_with_a_trigger_replaces_the_authored_trigger():
    from core.schedule import parse_schedule_yaml

    moved = parse_schedule_yaml(_AUTHORED, [{"name": "brief", "every": "2h"}])
    assert moved[0].trigger.kind == "every" and moved[0].trigger.interval_s == 7200


def test_a_new_name_adds_a_row_and_unnamed_rows_are_untouched():
    from core.schedule import parse_schedule_yaml

    out = parse_schedule_yaml(_AUTHORED, [{"name": "walk", "directive": "think", "at": "19:00", "topic": "walk"}])
    assert [e.directive for e in out] == ["brief", "think", "think"]
    assert out[1].trigger.kind == "idle" and out[2].name == "walk"


def test_a_malformed_yaml_schedule_is_empty_never_raises():
    from core.schedule import parse_schedule_yaml

    assert parse_schedule_yaml("schedule: [unclosed") == []
    assert parse_schedule_yaml("just text") == []


def test_settings_yaml_schedule_rows_reach_the_config(tmp_path):
    from core.config import load_config

    (tmp_path / "settings.yaml").write_text(
        "schedule:\n  - name: morning-brief\n    enabled: false\n", encoding="utf-8")
    cfg = load_config(load_env=False, settings_dir=tmp_path)
    assert cfg.schedule_overrides == ({"name": "morning-brief", "enabled": False},)
    entries = load_schedule(cfg.schedule_path, cfg.schedule_overrides)
    brief = next(e for e in entries if e.name == "morning-brief")
    assert brief.enabled is False


@pytest.mark.parametrize("section, problem", [
    ("schedule:\n  - enabled: false\n", "needs a name"),
    ("schedule:\n  - name: x\n    at: 19:00\n", "quoted time"),  # YAML 1.1: an unquoted 19:00 is 1140
    ("schedule:\n  - name: x\n    enabled: maybe\n", "true or false"),
    ("schedule:\n  - name: x\n    days: [someday]\n", "mon, tue"),
    ("schedule:\n  name: x\n", "must be a list"),
])
def test_a_wrong_schedule_section_refuses_readably(tmp_path, section, problem):
    from core.config import load_config
    from core.settings import SettingsError

    (tmp_path / "settings.yaml").write_text(section, encoding="utf-8")
    with pytest.raises(SettingsError) as err:
        load_config(load_env=False, settings_dir=tmp_path)
    assert problem in str(err.value)


def test_the_schedule_section_never_belongs_in_secrets(tmp_path):
    from core.config import load_config
    from core.settings import SettingsError

    (tmp_path / "secrets.yaml").write_text("schedule:\n  - name: x\n", encoding="utf-8")
    (tmp_path / "secrets.yaml").chmod(0o600)
    with pytest.raises(SettingsError, match="belongs in settings.yaml"):
        load_config(load_env=False, settings_dir=tmp_path)

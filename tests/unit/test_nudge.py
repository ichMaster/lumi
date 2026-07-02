"""Unit tests for the idle-nudge logic (LUMI-022) — pure, clock-driven, no sleeps."""

from datetime import UTC, datetime, timedelta

from core.config import load_config
from core.nudge import load_nudges, should_nudge

_T0 = datetime(2026, 6, 7, 14, 0, tzinfo=UTC)


def test_should_nudge_fires_after_the_interval():
    assert should_nudge(_T0, _T0 + timedelta(seconds=240), 240) is True
    assert should_nudge(_T0, _T0 + timedelta(seconds=239), 240) is False


def test_quiet_hours_suppress_the_nudge():
    last = datetime(2026, 6, 7, 1, 0, tzinfo=UTC)
    night = datetime(2026, 6, 7, 2, 0, tzinfo=UTC)  # 02:00 — inside 23–7 (wraps)
    assert should_nudge(last, night, 0, quiet_hours=(23, 7)) is False
    day = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)  # outside quiet, and idle since 01:00
    assert should_nudge(last, day, 0, quiet_hours=(23, 7)) is True


def test_load_nudges_from_the_authored_file():
    # The nudge file is authored / user-editable, so assert the loading behaviour,
    # not specific lines: a non-empty list with comments and blanks stripped.
    nudges = load_nudges(load_config(load_env=False).nudge_path)
    assert nudges  # at least one opener
    assert all(n.strip() for n in nudges)  # no blank lines
    assert all(not n.startswith("#") for n in nudges)  # comments skipped


def test_load_nudges_missing_file_is_empty(tmp_path):
    assert load_nudges(tmp_path / "nope.md") == []


def test_nudge_and_think_pace_independently():
    # The decoupling: each proactive mechanism keeps its OWN last-fired stamp, so a short-fuse
    # think firing never resets a long-fuse nudge's idle clock (the bug this fixes).
    from datetime import UTC, datetime, timedelta

    from core.nudge import proactive_due

    t0 = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)
    activity = t0                  # last real user input
    nudge_ts = think_ts = t0       # each mechanism's own last fire
    THINK, NUDGE = 300, 1200       # 5 min vs 20 min

    t5 = t0 + timedelta(seconds=300)
    assert proactive_due(activity, think_ts, t5, THINK)      # think is due at 5 min…
    think_ts = t5                                            # …fires, advancing ONLY its own stamp
    assert not proactive_due(activity, nudge_ts, t5, NUDGE)  # nudge not yet (still its own t0)

    t20 = t0 + timedelta(seconds=1200)
    # The think fired 3×/15min meanwhile, but never touched the nudge's clock → the nudge matures:
    assert proactive_due(activity, nudge_ts, t20, NUDGE)     # nudge fires at 20 min (not starved)


def test_proactive_due_respects_real_input_and_quiet_hours():
    from datetime import UTC, datetime, timedelta

    from core.nudge import proactive_due

    t0 = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)
    later = t0 + timedelta(seconds=1200)
    assert proactive_due(t0, t0, later, 1200)               # idle long enough → due
    assert not proactive_due(later, t0, later, 1200)        # real input just now → not idle
    assert not proactive_due(t0, t0, later, 1200, (0, 23))  # within quiet hours → suppressed


def test_think_seeds_file_loads_and_parses():
    # The new separate seed file: every non-comment line is a valid, REGISTERED %directive — the
    # authored seeds may use any directive in the registry (v0.33 tool-thoughts included), not just
    # the original %think/%wonder pair.
    from core.config import load_config
    from core.thoughts import REGISTRY, parse_directive

    seeds = load_nudges(load_config(load_env=False).think_seeds_path)
    assert seeds  # at least one seed
    for line in seeds:
        assert line.startswith("%")
        parsed = parse_directive(line)
        assert parsed is not None and parsed.name in REGISTRY


def test_pick_nudge_index_avoids_immediate_repeat():
    from core.nudge import pick_nudge_index

    assert pick_nudge_index(1, 0) == 0          # single opener → itself
    assert pick_nudge_index(2, 0) == 1          # with 2, the only non-repeat
    assert pick_nudge_index(2, 1) == 0
    for _ in range(60):                         # never repeats the last
        assert pick_nudge_index(5, 2) != 2

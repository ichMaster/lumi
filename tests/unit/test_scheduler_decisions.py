"""The brain's clock and bus are host-neutral (v2.5 LUMI-223) — the act decisions both hosts share, and the
modules importable without the TUI. Pure data; no model, no network."""

import subprocess
import sys
from pathlib import Path

from core.schedule import ScheduleEntry, Trigger
from core.scheduler import is_loud, scheduled_text, spoken_seed

_REPO = Path(__file__).resolve().parents[2]


def _entry(**kw) -> ScheduleEntry:
    return ScheduleEntry(trigger=Trigger(kind="every", interval_s=600), **{"directive": "think", **kw})


def test_an_entry_fires_its_directive_and_topic():
    assert scheduled_text(_entry(directive="catchup", topic="свіжі новини"), {}) == "%catchup свіжі новини"
    assert scheduled_text(_entry(), {}) == "%think"


def test_a_seeds_row_picks_a_line_and_never_repeats_it_at_once(tmp_path):
    seeds = tmp_path / "seeds.md"
    seeds.write_text("# a menu\n%think про море\n\n%wonder про зорі\n", encoding="utf-8")
    entry = _entry(directive="seeds", seeds=str(seeds))
    picks: dict[str, int] = {}
    fired = [scheduled_text(entry, picks) for _ in range(6)]
    assert set(fired) <= {"%think про море", "%wonder про зорі"}
    assert all(a != b for a, b in zip(fired, fired[1:], strict=False))  # no immediate repeat
    assert entry.id in picks


def test_an_empty_or_missing_seeds_file_fires_nothing(tmp_path):
    (tmp_path / "empty.md").write_text("# only a comment\n", encoding="utf-8")
    assert scheduled_text(_entry(seeds=str(tmp_path / "empty.md")), {}) == ""
    assert scheduled_text(_entry(seeds=str(tmp_path / "missing.md")), {}) == ""


def test_loud_means_the_row_asks_or_the_seed_is_open():
    assert is_loud(_entry(show=True), "silent") is True
    assert is_loud(_entry(show=False), "open") is True
    assert is_loud(_entry(show=False), "silent") is False


def test_the_spoken_seed_is_the_one_she_always_had():
    assert spoken_seed("море шумить") == (
        "(ти щойно подумала: «море шумить» — напиши йому перша, коротко поділись цим або почни з цього розмову)"
    )


def test_the_tui_uses_the_shared_decisions_not_inline_copies():
    src = (_REPO / "tui" / "app.py").read_text(encoding="utf-8")
    assert "ти щойно подумала" not in src  # the seed lives in one place (core.scheduler.spoken_seed)
    assert "is_loud(" in src and "scheduled_text(" in src and "spoken_seed(" in src


def test_any_host_imports_the_clock_and_the_bus_without_the_tui():
    probe = ("import sys, core.scheduler, state.bus; "
             "leaked = [m for m in sys.modules if m == 'tui' or m.startswith('tui.') or m.startswith('textual')]; "
             "assert not leaked, leaked")
    done = subprocess.run([sys.executable, "-c", probe], cwd=_REPO, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr


def test_the_old_tui_modules_still_re_export():
    from core.scheduler import Scheduler
    from state.bus import drain_inbox_records, make_photo_sink
    from tui.bridge import drain_inbox_records as old_drain
    from tui.bridge import make_photo_sink as old_sink
    from tui.scheduler import Scheduler as OldScheduler

    assert old_drain is drain_inbox_records and old_sink is make_photo_sink and OldScheduler is Scheduler

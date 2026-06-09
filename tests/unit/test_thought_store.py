"""Global Thought store — dated diary, A→B isolation, soft age cap (v0.12, LUMI-046)."""

import json

from core.repository import Thought, make_thought
from state.local_store import JsonRepository


def _add(repo, when, user, text="…", kind="think"):
    repo.add_thought(make_thought(when, kind, text, "calm", ["mood"], user, ts="2026-06-09T00:00:00"))


def test_add_and_read_global_dated(tmp_path):
    repo = JsonRepository(tmp_path / "s.json")
    _add(repo, "2026-06-09T18:00", "owner", "evening")
    _add(repo, "2026-06-09T08:00", "owner", "morning")
    got = repo.thoughts_since("2026-06-09T00:00")
    assert [t.text for t in got] == ["morning", "evening"]  # dated order, not insertion order


def test_window_excludes_older_than_since(tmp_path):
    repo = JsonRepository(tmp_path / "s.json")
    _add(repo, "2026-06-08T08:00", "owner", "yesterday")
    _add(repo, "2026-06-09T08:00", "owner", "today")
    assert [t.text for t in repo.thoughts_since("2026-06-09T00:00")] == ["today"]


def test_isolation_a_never_surfaces_to_b(tmp_path):
    repo = JsonRepository(tmp_path / "s.json")
    _add(repo, "2026-06-09T08:00", "alice", "alice secret")
    _add(repo, "2026-06-09T09:00", "bob", "bob thought")
    # the raw global stream holds both…
    assert len(repo.thoughts_since("2026-06-09T00:00")) == 2
    # …but per-user surfacing isolates: B never sees A's thought.
    surfaced = repo.thoughts_for("bob", "2026-06-09T00:00")
    assert [t.text for t in surfaced] == ["bob thought"]
    assert all(t.user_id == "bob" for t in surfaced)
    assert [t.text for t in repo.thoughts_for("alice", "2026-06-09T00:00")] == ["alice secret"]


def test_store_is_a_global_list_not_user_keyed(tmp_path):
    p = tmp_path / "s.json"
    repo = JsonRepository(p)
    _add(repo, "2026-06-09T08:00", "owner", "x")
    section = json.loads(p.read_text(encoding="utf-8"))["thoughts"]
    assert isinstance(section, list)  # a flat global list, NOT {user_id: [...]}


def test_prune_drops_old(tmp_path):
    repo = JsonRepository(tmp_path / "s.json")
    _add(repo, "2026-06-01T08:00", "owner", "old")
    _add(repo, "2026-06-09T08:00", "owner", "new")
    repo.prune_thoughts("2026-06-05T00:00")
    assert [t.text for t in repo.thoughts_since("2026-01-01")] == ["new"]


def test_round_trip_persist(tmp_path):
    p = tmp_path / "s.json"
    JsonRepository(p).add_thought(
        make_thought("2026-06-09T08:00", "wonder", "hi", "joy", ["mood", "recent"], "owner",
                     spoken=True, ts="t1")
    )
    reloaded = JsonRepository(p).thoughts_since("2026-01-01")[0]
    assert reloaded == Thought("2026-06-09T08:00", "wonder", "hi", "joy",
                               ("mood", "recent"), "owner", True, "t1")
    assert reloaded.seeds == ("mood", "recent")  # list→tuple coercion held across the round-trip

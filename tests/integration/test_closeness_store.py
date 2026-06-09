"""Closeness persistence + per-user isolation (v0.10, LUMI-037)."""

from core.repository import Closeness, LongTermFact
from state.local_store import JsonRepository


def _c(user_id, value=50.0, level=3, ts="2026-06-09T10:00:00+00:00"):
    return Closeness(user_id, value, level, ts)


def test_closeness_persists_and_reloads(tmp_path):
    p = tmp_path / "store.json"
    JsonRepository(p).set_closeness(_c("owner", value=62.5, level=4))
    reloaded = JsonRepository(p).get_closeness("owner")
    assert reloaded.value == 62.5 and reloaded.level == 4 and reloaded.user_id == "owner"


def test_closeness_upserts_by_user(tmp_path):
    repo = JsonRepository(tmp_path / "store.json")
    repo.set_closeness(_c("owner", value=10.0, level=1))
    repo.set_closeness(_c("owner", value=80.0, level=5))  # upsert
    assert repo.get_closeness("owner").level == 5


def test_closeness_is_isolated_by_user(tmp_path):
    repo = JsonRepository(tmp_path / "store.json")
    repo.set_closeness(_c("alice", value=90.0, level=5))
    assert repo.get_closeness("alice").level == 5
    assert repo.get_closeness("bob") is None  # B never sees A's closeness


def test_clear_memory_drops_closeness(tmp_path):
    repo = JsonRepository(tmp_path / "store.json")
    repo.set_closeness(_c("owner"))
    repo.add_fact(LongTermFact("owner", "loves tea", "", 0.5, "2026-06-09T10:00:00+00:00"))
    repo.clear_memory("owner")
    assert repo.get_closeness("owner") is None


def test_missing_closeness_is_none(tmp_path):
    assert JsonRepository(tmp_path / "store.json").get_closeness("nobody") is None

"""Contract test: the per-user isolation invariant (LUMI-007).

A record written under user A is never retrievable in user B's context. Pinned at
the data level from v0.2 (auth-boundary enforcement comes in v1.3). ARCHITECTURE
§Identity, users, and memory scopes — "the isolation invariant (hard rule)".
"""

from core.repository import make_message
from state.local_store import JsonRepository


def test_sessions_are_isolated_by_user(tmp_path):
    repo = JsonRepository(tmp_path / "store.json")

    alice = repo.create_session("alice")
    repo.append_message(make_message(alice.id, "alice", "user", "Alice's secret"))
    bob = repo.create_session("bob")

    alice_ids = {s.id for s in repo.list_sessions("alice")}
    bob_ids = {s.id for s in repo.list_sessions("bob")}

    # Each user sees only their own sessions — never the other's.
    assert alice_ids == {alice.id}
    assert bob_ids == {bob.id}
    assert alice.id not in bob_ids
    assert bob.id not in alice_ids


def test_a_users_session_does_not_leak_into_another_users_list(tmp_path):
    repo = JsonRepository(tmp_path / "store.json")
    repo.create_session("alice")
    # Bob has no sessions; Alice's must not appear under Bob.
    assert repo.list_sessions("bob") == []

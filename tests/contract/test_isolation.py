"""Contract test: the per-user isolation invariant (LUMI-007).

A record written under user A is never retrievable in user B's context. Pinned at
the data level from v0.2 (auth-boundary enforcement comes in v2.3). ARCHITECTURE
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


# --- v3 Agora: the isolation invariant restated for spaces ---------------------------------------------------------


def test_a_space_lives_in_a_file_of_its_own_with_no_user_id(tmp_path):
    """A space's records never land in the relationship store, and the space store holds no ``user_id`` at all —
    a rollback drops one file, and no space line is ever readable as a user's memory."""
    import sqlite3

    from core.space import SpaceMessage
    from state.space_store import SqliteSpaceStore

    repo = JsonRepository(tmp_path / "store.json")
    owner = repo.create_session("owner")
    repo.append_message(make_message(owner.id, "owner", "user", "his own line"))
    store = SqliteSpaceStore(tmp_path / "spaces.sqlite")
    store.add_lines([SpaceMessage(channel="agora", session_id="agora-2026-11-16", speaker="Ада", role="user",
                                  text="рядок кімнати", ts="2026-11-16T08:01:12+02:00", source_id="$e")])

    raw = sqlite3.connect(tmp_path / "spaces.sqlite")
    columns = {row[1] for table in ("space_messages", "space_vectors", "space_gists")
               for row in raw.execute(f"PRAGMA table_info({table})")}
    data = " ".join(row[0] for row in raw.execute("SELECT data FROM space_messages"))
    raw.close()
    assert "user_id" not in columns and "user_id" not in data
    assert "his own line" not in data  # his records never in the space store
    texts = [m.text for s in repo.list_sessions("owner") for m in repo.load_messages(s.id)]
    assert texts == ["his own line"]  # the room's line never in his
    assert (tmp_path / "store.json").read_text(encoding="utf-8").count("рядок кімнати") == 0

"""v1.5 LUMI-193 — the SQLite backend passes the same Repository contract as the JSON store.

One parametrized suite runs the storage contract against BOTH backends; SQLite-specific behaviors
(O(1) persist, adoption of an existing store.json, reopen) are pinned separately.
"""
from __future__ import annotations

import json

import pytest

from core.repository import Closeness, LongTermFact, Message, ShortSummary, Thought, VectorRecord
from state.local_store import JsonRepository
from state.sqlite_store import SqliteRepository

BACKENDS = {"json": JsonRepository, "sqlite": SqliteRepository}


@pytest.fixture(params=sorted(BACKENDS))
def repo(request, tmp_path):
    return BACKENDS[request.param](tmp_path / "store.json")


def _msg(session_id, user_id, role, text, ts="2026-07-21T10:00:00+00:00", **kw) -> Message:
    return Message(session_id=session_id, user_id=user_id, role=role, text=text, ts=ts, **kw)


# --- the shared contract (runs against both backends) --------------------------------------------

def test_messages_round_trip_in_order(repo):
    s = repo.create_session("owner")
    repo.append_message(_msg(s.id, "owner", "user", "раз"))
    repo.append_message(_msg(s.id, "owner", "lili", "два", emotion="calm", intensity=0.5, intent="deepen"))
    msgs = repo.load_messages(s.id)
    assert [(m.role, m.text) for m in msgs] == [("user", "раз"), ("lili", "два")]
    assert msgs[1].emotion == "calm" and msgs[1].intent == "deepen"  # full record survives


def test_messages_are_scoped_to_their_session(repo):
    a, b = repo.create_session("owner"), repo.create_session("owner")
    repo.append_message(_msg(a.id, "owner", "user", "в сесії A"))
    assert repo.load_messages(b.id) == []


def test_per_user_isolation_across_sessions(repo):
    sa, sb = repo.create_session("alice"), repo.create_session("bob")
    repo.append_message(_msg(sa.id, "alice", "user", "секрет Аліси"))
    repo.append_message(_msg(sb.id, "bob", "user", "нотатка Боба"))
    texts_b = [m.text for m in repo.load_messages(sb.id)]
    assert "секрет Аліси" not in texts_b  # A's record never readable in B's scope


def test_sessions_and_summaries_round_trip(repo):
    s = repo.create_session("owner")
    assert repo.get_session(s.id) is not None
    repo.add_summary(ShortSummary(user_id="owner", session_id=s.id, summary="підсумок",
                                  gist="суть", ts="2026-07-21T10:00:00+00:00"))
    assert repo.recent_summaries("owner")[-1].summary == "підсумок"


def test_closeness_round_trip_and_update(repo):
    assert repo.get_closeness("owner") is None
    repo.set_closeness(Closeness(user_id="owner", value=0.4, level=2, last_ts="2026-07-21T10:00:00+00:00"))
    repo.set_closeness(Closeness(user_id="owner", value=0.5, level=3, last_ts="2026-07-21T11:00:00+00:00"))
    got = repo.get_closeness("owner")
    assert (got.value, got.level) == (0.5, 3)  # the update won (no duplicates)


def test_facts_and_clear_memory(repo):
    repo.add_fact(LongTermFact(user_id="owner", fact="любить каву", meta="", confidence=0.9,
                               ts="2026-07-21T10:00:00+00:00"))
    repo.set_closeness(Closeness(user_id="owner", value=0.4, level=2, last_ts="2026-07-21T10:00:00+00:00"))
    s = repo.create_session("owner")
    repo.append_message(_msg(s.id, "owner", "user", "привіт"))
    repo.clear_memory("owner")
    assert repo.facts("owner") == []
    assert repo.get_closeness("owner") is None
    assert len(repo.load_messages(s.id)) == 1  # /forget clears memory, never the raw history (parity)


def test_thoughts_are_global(repo):
    repo.add_thought(Thought(when="2026-07-21T10:00", kind="think", text="думка",
                             emotion="calm", seeds=("mood",), user_id="owner"))
    assert [t.text for t in repo.thoughts_since("2026-07-21")] == ["думка"]


def test_vectors_still_work(repo):
    repo.add_vector(VectorRecord(user_id="owner", msg_id="m1", vector=[1.0, 0.0], text="привіт",
                                 ts="2026-07-21T10:00:00+00:00", role="user"))
    hits = repo.search_vectors("owner", [1.0, 0.0], k=1)
    assert hits and hits[0][1].msg_id == "m1"


def test_reopen_preserves_everything(repo, tmp_path):
    s = repo.create_session("owner")
    repo.append_message(_msg(s.id, "owner", "user", "збережи мене"))
    repo.set_closeness(Closeness(user_id="owner", value=0.4, level=2, last_ts="2026-07-21T10:00:00+00:00"))
    reopened = type(repo)(tmp_path / "store.json")
    assert [m.text for m in reopened.load_messages(s.id)] == ["збережи мене"]
    assert reopened.get_closeness("owner").level == 2


# --- SQLite-specific pins ------------------------------------------------------------------------

def test_sqlite_append_is_o1_store_json_does_not_grow(tmp_path):
    repo = SqliteRepository(tmp_path / "store.json")
    s = repo.create_session("owner")
    size_before = (tmp_path / "store.json").stat().st_size
    for i in range(50):
        repo.append_message(_msg(s.id, "owner", "user", f"повідомлення {i}"))
    assert (tmp_path / "store.json").stat().st_size == size_before  # no rewrite per message
    assert len(repo.load_messages(s.id)) == 50                      # all in the DB


def test_sqlite_adopts_an_existing_json_store(tmp_path):
    old = JsonRepository(tmp_path / "store.json")
    s = old.create_session("owner")
    old.append_message(_msg(s.id, "owner", "user", "стара історія"))
    old.set_closeness(Closeness(user_id="owner", value=0.3, level=1, last_ts="2026-07-21T09:00:00+00:00"))

    new = SqliteRepository(tmp_path / "store.json")                 # first open over the JSON store
    assert [m.text for m in new.load_messages(s.id)] == ["стара історія"]  # imported into the DB
    assert new.get_closeness("owner").level == 1
    on_disk = json.loads((tmp_path / "store.json").read_text(encoding="utf-8"))
    assert on_disk.get("messages", {}) == {}                        # store.json shrank

    again = SqliteRepository(tmp_path / "store.json")               # idempotent second open
    assert len(again.load_messages(s.id)) == 1                      # no duplicates


def test_sqlite_legacy_move_field_is_migrated_on_read(tmp_path):
    repo = SqliteRepository(tmp_path / "store.json")
    s = repo.create_session("owner")
    repo._db.execute(  # simulate a legacy row written before the v1.1 move→intent rename
        "INSERT INTO messages (session_id, user_id, data) VALUES (?, ?, ?)",
        (s.id, "owner", json.dumps({"session_id": s.id, "user_id": "owner", "role": "lili",
                                    "text": "старий", "ts": "2026-07-21T10:00:00+00:00",
                                    "move": "deepen", "unknown_key": 1})),
    )
    repo._db.commit()
    msg = repo.load_messages(s.id)[0]
    assert msg.intent == "deepen"  # move carried over; unknown keys dropped (same shim as JSON)

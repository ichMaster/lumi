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


def _vec(user, mid, vector, kind="message"):
    return VectorRecord(user_id=user, msg_id=mid, vector=vector, text=f"t-{mid}",
                        ts="2026-07-21T10:00:00+00:00", role="user", kind=kind)


def test_vector_kind_scoping(repo):
    repo.add_vectors([_vec("owner", "m1", [1.0, 0.0]), _vec("owner", "f1", [1.0, 0.0], kind="fact")])
    assert [r.msg_id for _, r in repo.search_vectors("owner", [1.0, 0.0], 10, kind="fact")] == ["f1"]
    assert [r.msg_id for _, r in repo.search_vectors("owner", [1.0, 0.0], 10, kind="message")] == ["m1"]


def test_vector_isolation_between_users(repo):
    repo.add_vectors([_vec("alice", "a1", [1.0, 0.0]), _vec("bob", "b1", [1.0, 0.0])])
    assert [r.msg_id for _, r in repo.search_vectors("bob", [1.0, 0.0], 10)] == ["b1"]


def test_vector_dedup_and_has_vector(repo):
    repo.add_vector(_vec("owner", "m1", [1.0, 0.0]))
    repo.add_vector(_vec("owner", "m1", [1.0, 0.0]))  # idempotent re-index
    assert len(repo.search_vectors("owner", [1.0, 0.0], 10)) == 1
    assert repo.has_vector("owner", "m1") and not repo.has_vector("owner", "nope")


def test_vector_reset_drops_all_and_sets_model(repo):
    repo.add_vector(_vec("owner", "m1", [1.0, 0.0]))
    repo.reset_vectors("new-model")
    assert repo.search_vectors("owner", [1.0, 0.0], 10) == []
    assert repo.vectors_model() == "new-model"


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


def test_sqlite_adopts_the_vectors_jsonl_without_reembedding(tmp_path):
    # v1.5 LUMI-194: an existing .vectors.jsonl re-packs into the DB — identical search results,
    # no embedder anywhere near the path, and the vectors never held in the memory dicts.
    old = JsonRepository(tmp_path / "store.json")
    old.add_vectors([_vec("owner", "m1", [0.9, 0.1]), _vec("owner", "m2", [0.1, 0.9]),
                     _vec("owner", "f1", [0.8, 0.2], kind="fact")])
    expected = [(round(s, 6), r.msg_id) for s, r in old.search_vectors("owner", [1.0, 0.0], 3)]

    new = SqliteRepository(tmp_path / "store.json")
    got = [(round(s, 6), r.msg_id) for s, r in new.search_vectors("owner", [1.0, 0.0], 3)]
    assert got == expected                                  # identical ranking after the re-pack
    assert new._vectors == {}                               # NOT resident in RAM (the S2b point)
    assert new.has_vector("owner", "m1")

    again = SqliteRepository(tmp_path / "store.json")       # second open: table non-empty → no rescan
    assert len(again.search_vectors("owner", [1.0, 0.0], 10)) == 3


def test_sqlite_float32_blob_keeps_the_cosine_ranking(tmp_path):
    # The float64→float32 round-trip must not change the ranking (tolerance ~1e-6 on scores).
    vecs = [("a", [0.123456789, 0.987654321]), ("b", [0.5, 0.5]), ("c", [0.9999999, 0.0000001])]
    j = JsonRepository(tmp_path / "j" / "store.json")
    s = SqliteRepository(tmp_path / "s" / "store.json")
    for mid, v in vecs:
        j.add_vector(_vec("owner", mid, v))
        s.add_vector(_vec("owner", mid, v))
    q = [0.7, 0.3]
    jr = [(r.msg_id, round(sc, 5)) for sc, r in j.search_vectors("owner", q, 3)]
    sr = [(r.msg_id, round(sc, 5)) for sc, r in s.search_vectors("owner", q, 3)]
    assert jr == sr


def test_sqlite_clear_memory_deletes_the_user_vectors(tmp_path):
    repo = SqliteRepository(tmp_path / "store.json")
    repo.add_vectors([_vec("alice", "a1", [1.0, 0.0]), _vec("bob", "b1", [1.0, 0.0])])
    repo.clear_memory("alice")
    assert repo.search_vectors("alice", [1.0, 0.0], 10) == []
    assert len(repo.search_vectors("bob", [1.0, 0.0], 10)) == 1  # untouched


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

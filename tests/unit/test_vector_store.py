"""Unit tests for the VectorStore seam (LUMI-062).

The per-user vector store behind the Repository: round-trip, idempotent indexing by
content-addressed msg_id, top-K cosine ranking, and per-user isolation (search runs
only over the requesting user's vectors). Uses the deterministic MockEmbedder — no
network, no paid APIs. The pure-Python cosine fallback is exercised when numpy isn't
installed (the `embed` extra); numpy is used when present.
"""

from core.embedder import MockEmbedder
from core.repository import Repository, VectorRecord, make_vector_record, vector_msg_id
from state.local_store import JsonRepository

OWNER = "owner"


def _index(repo, embedder, user_id, session_id, role, text, ts):
    [vec] = embedder.embed([text])
    rec = make_vector_record(
        user_id=user_id, session_id=session_id, role=role, text=text, ts=ts, vector=vec
    )
    repo.add_vector(rec)
    return rec


def test_jsonrepo_still_satisfies_repository_protocol(tmp_path):
    # The additive vector methods don't break the seam.
    assert isinstance(JsonRepository(tmp_path / "s.json"), Repository)


def test_msg_id_is_stable_and_content_addressed():
    a = vector_msg_id("s1", "2026-06-06T10:00:00", "user", "привіт")
    b = vector_msg_id("s1", "2026-06-06T10:00:00", "user", "привіт")
    c = vector_msg_id("s1", "2026-06-06T10:00:00", "user", "інший текст")
    assert a == b          # same inputs → same id
    assert a != c          # different text → different id


def test_add_and_search_round_trip(tmp_path):
    repo = JsonRepository(tmp_path / "s.json")
    e = MockEmbedder()
    _index(repo, e, OWNER, "s1", "user", "я люблю каву вранці", "2026-06-06T10:00:00")
    [vq] = e.embed(["кава вранці"])
    hits = repo.search_vectors(OWNER, list(vq), k=5)
    assert len(hits) == 1
    score, rec = hits[0]
    assert rec.text == "я люблю каву вранці"
    assert score > 0.0


def test_top_k_ranks_by_cosine_descending_and_caps(tmp_path):
    repo = JsonRepository(tmp_path / "s.json")
    e = MockEmbedder()
    _index(repo, e, OWNER, "s1", "user", "кава молоко цукор", "2026-06-06T10:00:00")
    _index(repo, e, OWNER, "s1", "user", "кава вранці смачна", "2026-06-06T10:01:00")
    _index(repo, e, OWNER, "s1", "user", "погода сьогодні похмура", "2026-06-06T10:02:00")
    [vq] = e.embed(["кава"])
    hits = repo.search_vectors(OWNER, list(vq), k=2)
    assert len(hits) == 2                                  # k caps the result count
    scores = [s for s, _ in hits]
    assert scores == sorted(scores, reverse=True)          # descending by similarity
    assert all("кава" in rec.text for _, rec in hits)      # coffee lines beat the weather line


def test_has_vector_and_idempotent_add(tmp_path):
    repo = JsonRepository(tmp_path / "s.json")
    e = MockEmbedder()
    rec = _index(repo, e, OWNER, "s1", "user", "запам'ятай", "2026-06-06T10:00:00")
    assert repo.has_vector(OWNER, rec.msg_id) is True
    assert repo.has_vector(OWNER, "nope") is False
    # Re-indexing the identical message upserts, never duplicates.
    _index(repo, e, OWNER, "s1", "user", "запам'ятай", "2026-06-06T10:00:00")
    [vq] = e.embed(["запам'ятай"])
    assert len(repo.search_vectors(OWNER, list(vq), k=10)) == 1


def test_vectors_persist_across_restart(tmp_path):
    path = tmp_path / "s.json"
    repo = JsonRepository(path)
    e = MockEmbedder()
    rec = _index(repo, e, OWNER, "s1", "lili", "я тут", "2026-06-06T10:00:00")

    reopened = JsonRepository(path)
    assert reopened.has_vector(OWNER, rec.msg_id) is True
    [vq] = e.embed(["я тут"])
    hits = reopened.search_vectors(OWNER, list(vq), k=1)
    assert hits and isinstance(hits[0][1], VectorRecord)
    assert hits[0][1].vector == rec.vector  # tuple round-trips intact


def test_cold_store_returns_empty(tmp_path):
    repo = JsonRepository(tmp_path / "s.json")
    assert repo.search_vectors(OWNER, [0.1, 0.2, 0.3], k=5) == []


def test_search_is_scoped_to_user(tmp_path):
    # Isolation: B's search never sees A's vectors (and vice versa).
    repo = JsonRepository(tmp_path / "s.json")
    e = MockEmbedder()
    _index(repo, e, "alice", "sa", "user", "секрет Аліси про каву", "2026-06-06T10:00:00")
    _index(repo, e, "bob", "sb", "user", "секрет Боба про каву", "2026-06-06T10:00:00")
    [vq] = e.embed(["кава"])
    bob_hits = repo.search_vectors("bob", list(vq), k=10)
    assert all(rec.user_id == "bob" for _, rec in bob_hits)
    assert all("Аліси" not in rec.text for _, rec in bob_hits)


def test_clear_memory_drops_only_that_users_vectors(tmp_path):
    repo = JsonRepository(tmp_path / "s.json")
    e = MockEmbedder()
    a = _index(repo, e, "alice", "sa", "user", "Аліса", "2026-06-06T10:00:00")
    b = _index(repo, e, "bob", "sb", "user", "Боб", "2026-06-06T10:00:00")
    repo.clear_memory("alice")
    assert repo.has_vector("alice", a.msg_id) is False
    assert repo.has_vector("bob", b.msg_id) is True

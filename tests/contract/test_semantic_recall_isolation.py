"""Contract test: per-user isolation + graceful degradation for semantic recall (LUMI-065).

The hard rule (ARCHITECTURE §Semantic recall, SEMANTIC_RECALL.md): retrieval — both the
vector store's ``search_vectors`` and ``Core.recall`` — runs **only** over the requesting
user's vectors. User A's messages can **never** surface for user B, the same isolation
invariant as the rest of memory. Plus the graceful-failure guarantees as contract checks.

Fails loudly if a future change lets vectors cross users. Mock embedder — no network,
no paid APIs.
"""

from core.agent import Core
from core.embedder import MockEmbedder
from core.llm import MockLLMClient
from core.repository import make_vector_record
from state.local_store import JsonRepository


def _vec(repo, embedder, user_id, session_id, role, text, ts="2026-06-06T10:00:00"):
    [v] = embedder.embed([text])
    repo.add_vector(make_vector_record(
        user_id=user_id, session_id=session_id, role=role, text=text, ts=ts, vector=v
    ))


def _core(repo, user_id, *, embedder, reply="привіт"):
    return Core(llm=MockLLMClient(reply), repository=repo, canon="Ти — Лілі.", model="m",
                user_id=user_id, embedder=embedder, recall_enabled=True)


# --- store level: search_vectors is scoped to the user --------------------
def test_search_vectors_never_crosses_users(tmp_path):
    repo = JsonRepository(tmp_path / "s.json")
    e = MockEmbedder()
    _vec(repo, e, "alice", "sa", "user", "секрет Аліси кава")
    _vec(repo, e, "bob", "sb", "user", "секрет Боба кава")

    [q] = e.embed(["кава"])
    alice_hits = repo.search_vectors("alice", list(q), k=10)
    bob_hits = repo.search_vectors("bob", list(q), k=10)

    assert alice_hits and all(rec.user_id == "alice" for _, rec in alice_hits)
    assert bob_hits and all(rec.user_id == "bob" for _, rec in bob_hits)
    assert all("Боба" not in rec.text for _, rec in alice_hits)   # A never sees B
    assert all("Аліси" not in rec.text for _, rec in bob_hits)    # B never sees A


# --- Core.recall level: both directions, across index-on-write ------------
def test_core_recall_is_isolated_both_directions(tmp_path):
    repo = JsonRepository(tmp_path / "s.json")  # one shared store, two users
    alice = _core(repo, "alice", embedder=MockEmbedder(), reply="привіт від Лілі Алісі")
    bob = _core(repo, "bob", embedder=MockEmbedder(), reply="привіт від Лілі Бобу")

    alice.reply("улюблена страва борщ", alice.start_session())
    bob.reply("улюблена страва піца", bob.start_session())

    alice_hits = alice.recall("улюблена страва")
    bob_hits = bob.recall("улюблена страва")

    assert any("борщ" in rec.text for _, rec in alice_hits)
    assert all("піца" not in rec.text for _, rec in alice_hits)   # A never sees B's line
    assert any("піца" in rec.text for _, rec in bob_hits)
    assert all("борщ" not in rec.text for _, rec in bob_hits)     # B never sees A's line


def test_backfill_indexes_only_the_requesting_user(tmp_path):
    # Backfill walks only the user's own sessions → never indexes another user's messages.
    repo = JsonRepository(tmp_path / "s.json")
    off_a = Core(llm=MockLLMClient("ок"), repository=repo, canon="c", model="m",
                 user_id="alice", embedder=MockEmbedder(), recall_enabled=False)
    off_b = Core(llm=MockLLMClient("ок"), repository=repo, canon="c", model="m",
                 user_id="bob", embedder=MockEmbedder(), recall_enabled=False)
    off_a.reply("Алісин рядок", off_a.start_session())
    off_b.reply("Бобів рядок", off_b.start_session())

    on_a = Core(llm=MockLLMClient("ок"), repository=repo, canon="c", model="m",
                user_id="alice", embedder=MockEmbedder(), recall_enabled=True)
    on_a.backfill_vectors()

    assert all(r.user_id == "alice" for r in repo._vectors.get("alice", []))
    assert repo._vectors.get("bob", []) == []  # B's messages were NOT touched by A's backfill


# --- graceful degradation --------------------------------------------------
class _BoomEmbedder:
    dim = 8

    def embed(self, texts):
        raise RuntimeError("embedding service down")


def test_embedder_error_never_breaks_a_turn_or_recall(tmp_path):
    repo = JsonRepository(tmp_path / "s.json")
    core = _core(repo, "owner", embedder=_BoomEmbedder())
    session = core.start_session()

    state = core.reply("привіт", session)            # the turn still completes
    assert state.reply
    msgs = repo.load_messages(session.id)
    assert [m.text for m in msgs] == ["привіт", "привіт"]  # messages still stored
    assert core.recall("привіт") == []                # recall degrades to [], never raises


# --- clear_memory drops only the cleared user's vectors -------------------
def test_clear_memory_is_per_user(tmp_path):
    repo = JsonRepository(tmp_path / "s.json")
    e = MockEmbedder()
    _vec(repo, e, "alice", "sa", "user", "Аліса")
    _vec(repo, e, "bob", "sb", "user", "Боб")

    repo.clear_memory("alice")

    assert repo._vectors.get("alice", []) == []          # A's vectors gone
    assert any(r.text == "Боб" for r in repo._vectors.get("bob", []))  # B's intact

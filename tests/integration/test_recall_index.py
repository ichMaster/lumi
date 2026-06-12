"""Index-on-write + backfill for semantic recall (LUMI-063).

Every message (user + Лілі) is embedded into the per-user vector store as it's written;
existing messages backfill once; an embedder failure never blocks or loses a turn. All
via the deterministic MockEmbedder — no network, no paid APIs.
"""

import pytest

from core.agent import Core
from core.embedder import MockEmbedder
from core.llm import MockLLMClient
from core.repository import vector_msg_id
from state.local_store import JsonRepository


def _core(tmp_path, *, embedder=None, recall=True, user_id="owner", llm=None):
    return Core(
        llm=llm or MockLLMClient("привіт"),
        repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.",
        model="m",
        user_id=user_id,
        embedder=embedder,
        recall_enabled=recall,
    )


def _indexed_texts(repo, user_id="owner"):
    # The set of message texts that have a vector in this user's store.
    return {r.text for r in repo._vectors.get(user_id, [])}


def test_each_message_indexed_on_write(tmp_path):
    e = MockEmbedder()
    core = _core(tmp_path, embedder=e)
    session = core.start_session()
    core.reply("я люблю каву", session)

    texts = _indexed_texts(core._repo)
    assert "я люблю каву" in texts          # the user's line
    assert "привіт" in texts                # Лілі's reply
    assert e.calls                          # the embedder was actually called


def test_recall_disabled_is_a_no_op(tmp_path):
    e = MockEmbedder()
    core = _core(tmp_path, embedder=e, recall=False)
    session = core.start_session()
    core.reply("привіт", session)
    assert core.recall_enabled is False
    assert _indexed_texts(core._repo) == set()  # nothing indexed
    assert e.calls == []                         # embedder never touched


def test_no_embedder_is_a_no_op(tmp_path):
    core = _core(tmp_path, embedder=None, recall=True)
    session = core.start_session()
    core.reply("привіт", session)
    assert core.recall_enabled is False          # recall needs an embedder
    assert _indexed_texts(core._repo) == set()


def test_backfill_indexes_only_unindexed_and_is_idempotent(tmp_path):
    # Run with recall OFF so messages persist WITHOUT vectors, then backfill.
    store = tmp_path / "store.json"
    off = Core(llm=MockLLMClient("отут"), repository=JsonRepository(store),
               canon="Ти — Лілі.", model="m", embedder=MockEmbedder(), recall_enabled=False)
    session = off.start_session()
    off.reply("перше", session)
    off.reply("друге", session)
    assert _indexed_texts(off._repo) == set()  # nothing indexed yet

    # New process: recall ON over the same store → backfill catches up once.
    on = Core(llm=MockLLMClient("отут"), repository=JsonRepository(store),
              canon="Ти — Лілі.", model="m", embedder=MockEmbedder(), recall_enabled=True)
    indexed = on.backfill_vectors()
    assert indexed == 4  # 2 user + 2 lili messages
    # Idempotent: a second pass finds nothing new (has_vector skips them all).
    assert on.backfill_vectors() == 0


def test_backfill_respects_limit(tmp_path):
    store = tmp_path / "store.json"
    off = Core(llm=MockLLMClient("ок"), repository=JsonRepository(store),
               canon="Ти — Лілі.", model="m", embedder=MockEmbedder(), recall_enabled=False)
    session = off.start_session()
    off.reply("a", session)
    off.reply("b", session)  # 4 messages total
    on = Core(llm=MockLLMClient("ок"), repository=JsonRepository(store),
              canon="Ти — Лілі.", model="m", embedder=MockEmbedder(), recall_enabled=True)
    assert on.backfill_vectors(limit=2) == 2   # bounded per pass
    assert on.backfill_vectors(limit=2) == 2   # the rest on the next pass
    assert on.backfill_vectors(limit=2) == 0


def test_ensure_backfill_drains_the_whole_history(tmp_path):
    # ensure_backfill must cover ALL un-indexed messages, not just one capped batch.
    store = tmp_path / "store.json"
    off = Core(llm=MockLLMClient("ок"), repository=JsonRepository(store),
               canon="Ти — Лілі.", model="m", embedder=MockEmbedder(), recall_enabled=False)
    session = off.start_session()
    for t in ("a", "b", "c"):
        off.reply(t, session)  # 6 messages total, nothing indexed (recall off)

    # A tiny per-pass cap (2) — ensure_backfill must loop until everything is covered.
    on = Core(llm=MockLLMClient("ок"), repository=JsonRepository(store),
              canon="Ти — Лілі.", model="m", embedder=MockEmbedder(),
              recall_enabled=True, recall_backfill_max=2)
    on.ensure_backfill()
    assert len(on._repo._vectors["owner"]) == 6   # all 6 indexed despite the cap of 2
    on.ensure_backfill()                          # idempotent — runs once per process
    assert len(on._repo._vectors["owner"]) == 6


class _BoomEmbedder:
    """An embedder that always raises — to prove index-on-write degrades gracefully."""

    dim = 8

    def embed(self, texts):
        raise RuntimeError("embedding service down")


def test_embedder_failure_does_not_break_the_turn(tmp_path):
    core = _core(tmp_path, embedder=_BoomEmbedder())
    session = core.start_session()
    # The turn still completes and returns a valid reply despite the embed error.
    state = core.reply("привіт", session)
    assert state.reply
    # The messages are still persisted (just not indexed) — recoverable by a later backfill.
    msgs = core._repo.load_messages(session.id)
    assert [m.text for m in msgs] == ["привіт", "привіт"]
    assert _indexed_texts(core._repo) == set()


def test_indexed_message_is_searchable(tmp_path):
    e = MockEmbedder()
    core = _core(tmp_path, embedder=e)
    session = core.start_session()
    core.reply("кава смачна вранці", session)
    [q] = e.embed(["кава вранці"])
    hits = core._repo.search_vectors("owner", list(q), k=5)
    assert any("кава" in rec.text for _, rec in hits)


@pytest.mark.parametrize("role,text", [("user", "перше"), ("lili", "привіт")])
def test_msg_id_matches_between_write_and_backfill(tmp_path, role, text):
    # The content-addressed id is identical whether indexed on write or by backfill —
    # so backfill never re-indexes an on-write message.
    e = MockEmbedder()
    core = _core(tmp_path, embedder=e)
    session = core.start_session()
    core.reply("перше", session)
    msgs = core._repo.load_messages(session.id)
    m = next(m for m in msgs if m.role == role)
    assert core._repo.has_vector("owner", vector_msg_id(m.session_id, m.ts, m.role, m.text))

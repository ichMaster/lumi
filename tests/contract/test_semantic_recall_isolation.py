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
from core.emotion import EmotionState
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


def _recall_tool_core(repo, user_id, *, embedder):
    return Core(llm=MockLLMClient("привіт"), repository=repo, canon="Ти — Лілі.", model="m",
                user_id=user_id, embedder=embedder, recall_enabled=True, recall_tool_enabled=True)


def test_recall_tool_is_single_user(tmp_path):
    # v0.31 LUMI-122: the model-callable `recall` tool inherits the isolation invariant (it wraps the
    # user-scoped recall_moments). Bob's tool searches the SAME store but only his own vectors.
    repo = JsonRepository(tmp_path / "s.json")
    alice = _recall_tool_core(repo, "alice", embedder=MockEmbedder())
    bob = _recall_tool_core(repo, "bob", embedder=MockEmbedder())
    alice.reply("секрет Аліси кава пароль", alice.start_session())
    alice.ensure_backfill()
    bob.reply("секрет Боба кава код", bob.start_session())
    bob.ensure_backfill()
    _, execute = bob._recall_tool_args()
    out = execute("recall", {"query": "секрет кава"})
    body = out["text"] if isinstance(out, dict) else str(out)
    assert "Аліси" not in body and "пароль" not in body   # B's recall tool never returns A's content


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


# === v0.17 — automatic per-turn RAG: isolation in the turn + bounds/graceful (LUMI-074) ===
RAG_HEADER = "# Релевантні моменти минулого"


def _rag_core(repo, user_id, *, floor=0.0, rag=True):
    return Core(
        llm=MockLLMClient("ок"), repository=repo, canon="Ти — Лілі.", model="m",
        user_id=user_id, embedder=MockEmbedder(), recall_enabled=True,
        rag_enabled=rag, rag_floor=floor, memory_window=2, compaction_batch=2,
    )


def _rag_block(core):
    sys = core.last_prompt["system"]
    return sys.split(RAG_HEADER, 1)[1] if RAG_HEADER in sys else ""


def test_per_turn_rag_never_crosses_users(tmp_path):
    # One shared store, two users; each turn's injected recall block (anchor + neighbours) must
    # contain ONLY the requesting user's messages — never the other's.
    repo = JsonRepository(tmp_path / "s.json")
    alice, bob = _rag_core(repo, "alice"), _rag_core(repo, "bob")
    sa, sb = alice.start_session(), bob.start_session()
    alice.reply("моя улюблена страва борщ український зі сметаною", sa)
    bob.reply("моя улюблена страва піца італійська з моцарелою", sb)
    for filler in ("як справи", "що нового", "розкажи жарт", "добраніч друже"):
        alice.reply(filler, sa)   # push the food line out of each user's window
        bob.reply(filler, sb)

    bob.reply("нагадай про піцу", sb)        # bob's turn → bob's recall block
    bob_block = _rag_block(bob)
    assert "борщ" not in bob_block and "український" not in bob_block   # never Alice's
    alice.reply("нагадай про борщ", sa)      # alice's turn → alice's recall block
    alice_block = _rag_block(alice)
    assert "піца" not in alice_block and "італійська" not in alice_block  # never Bob's


def test_rag_off_is_byte_for_byte_the_pre_rag_prompt(tmp_path):
    # LUMI_RAG off → the prompt is exactly what it would be without the feature (no block).
    repo_on = JsonRepository(tmp_path / "on.json")
    repo_off = JsonRepository(tmp_path / "off.json")
    on, off = _rag_core(repo_on, "owner", rag=True), _rag_core(repo_off, "owner", rag=False)
    for c in (on, off):
        s = c.start_session()
        c.reply("пуер на третій заварці найсмачніший", s)
        for f in ("привіт", "як ти", "що робиш", "добраніч"):
            c.reply(f, s)
        c.reply("нагадай про пуер", s)
    assert RAG_HEADER not in off.last_prompt["system"]   # off → no block at all
    assert RAG_HEADER in on.last_prompt["system"]        # on → the block is there


def test_rag_below_floor_injects_no_block(tmp_path):
    repo = JsonRepository(tmp_path / "s.json")
    core = _rag_core(repo, "owner", floor=0.99)          # nothing clears a 0.99 floor
    s = core.start_session()
    core.reply("пуер на третій заварці", s)
    for f in ("привіт", "як ти", "добраніч"):
        core.reply(f, s)
    core.reply("нагадай про пуер", s)
    assert RAG_HEADER not in core.last_prompt["system"]


def test_rag_deduped_against_the_window(tmp_path):
    # A line still in the live window is never repeated in the block (no double-context).
    repo = JsonRepository(tmp_path / "s.json")
    core = Core(
        llm=MockLLMClient("ок"), repository=repo, canon="Ти — Лілі.", model="m",
        user_id="owner", embedder=MockEmbedder(), recall_enabled=True,
        rag_enabled=True, rag_floor=0.0, memory_window=40,  # big window → the line stays in it
    )
    s = core.start_session()
    core.reply("пуер на третій заварці унікальний", s)
    core.reply("розкажи про пуер заварці унікальний", s)  # matches the still-in-window line
    block = _rag_block(core)
    assert "третій заварці унікальний" not in block       # deduped (already in the window)


class _BoomEmbedder:
    dim = 8

    def embed(self, texts, *, is_query=False):
        raise RuntimeError("embedding down")


def test_rag_retrieval_error_never_breaks_the_turn(tmp_path):
    repo = JsonRepository(tmp_path / "s.json")
    core = Core(
        llm=MockLLMClient("ок"), repository=repo, canon="Ти — Лілі.", model="m",
        user_id="owner", embedder=_BoomEmbedder(), recall_enabled=True, rag_enabled=True,
    )
    s = core.start_session()
    state = core.reply("привіт", s)                       # the turn still completes…
    assert state.reply
    assert RAG_HEADER not in core.last_prompt["system"]   # …with no block (graceful)


# === v0.30 LUMI-119 — chunking: isolation + off-equivalence + graceful (over chunks) ==============
def _chunk_rag_core(repo, user_id, *, on=True):
    return Core(
        llm=MockLLMClient("ок"), repository=repo, canon="Ти — Лілі.", model="m",
        user_id=user_id, embedder=MockEmbedder(), recall_enabled=True, embed_model="m@x",
        rag_enabled=True, rag_floor=0.0, rag_k=5, rag_max_chars=8000, rag_snippet_chars=4000,
        rag_chunk=on, rag_chunk_chars=40, rag_chunk_overlap=8, rag_chunk_threshold=30, rag_chunk_w=1,
        memory_window=2, compaction_batch=2,
    )


# a long message with distinct tokens per region (so a query lands on one chunk)
_ALONG = " ".join(f"альфа{i} барвінок{i} деталь{i}" for i in range(10))


def test_chunked_recall_never_crosses_users(tmp_path):
    # A indexes a long (chunked) message; B's search never returns A's chunk — nor any of its
    # chunk/message neighbours (search is scoped to the requesting user).
    repo = JsonRepository(tmp_path / "s.json")
    alice, bob = _chunk_rag_core(repo, "alice"), _chunk_rag_core(repo, "bob")
    alice.reply(_ALONG, alice.start_session())
    bob.reply("моя нотатка зовсім про інше", bob.start_session())
    alice.ensure_backfill()
    bob.ensure_backfill()
    bob_hits = bob.recall("барвінок5")
    assert all(rec.user_id == "bob" for _, rec in bob_hits)        # only B's vectors
    assert all("барвінок5" not in rec.text for _, rec in bob_hits)  # never A's chunk
    a_hits = alice.recall("барвінок5")
    assert any("барвінок5" in rec.text for _, rec in a_hits)        # A recalls its own passage
    assert all(rec.user_id == "alice" for _, rec in a_hits)


def test_chunked_per_turn_rag_block_is_single_user(tmp_path):
    # The injected recall block (passage + its neighbours) on B's turn carries only B's messages.
    repo = JsonRepository(tmp_path / "s.json")
    alice, bob = _chunk_rag_core(repo, "alice"), _chunk_rag_core(repo, "bob")
    sa, sb = alice.start_session(), bob.start_session()
    alice.reply(_ALONG, sa)
    bob.reply("нейтральний рядок боба перший", sb)
    for f in ("як справи", "що нового", "добраніч друже"):
        alice.reply(f, sa)
        bob.reply(f, sb)
    bob.reply("нагадай барвінок5", sb)          # B's turn → B's recall block
    block = _rag_block(bob)
    assert "барвінок" not in block and "альфа" not in block   # never A's chunks/passage/neighbours


def test_chunk_off_block_equals_v017_for_short_messages(tmp_path):
    # A short message is one chunk, so chunking-on must produce the SAME recall block as chunking-off.
    on = _chunk_rag_core(JsonRepository(tmp_path / "on.json"), "owner", on=True)
    off = _chunk_rag_core(JsonRepository(tmp_path / "off.json"), "owner", on=False)
    for c in (on, off):
        s = c.start_session()
        c.reply("кодове пуершмуер тут", s)       # ~20 chars < the 30-char threshold → one chunk
        for f in ("привіт", "як ти", "добраніч"):
            c.reply(f, s)
        c.reply("нагадай пуершмуер", s)
    assert _rag_block(on) == _rag_block(off)    # chunking is a no-op for short messages


def test_chunked_turn_degrades_on_embedder_error(tmp_path):
    # An embedder failure with chunking on still completes the turn with no block (never raises).
    repo = JsonRepository(tmp_path / "s.json")
    core = Core(
        llm=MockLLMClient("ок"), repository=repo, canon="c", model="m", user_id="owner",
        embedder=_BoomEmbedder(), recall_enabled=True, rag_enabled=True,
        rag_chunk=True, rag_chunk_threshold=10,
    )
    s = core.start_session()
    state = core.reply("довге повідомлення яке мало б розбитися на чанки напевно так", s)
    assert state.reply and RAG_HEADER not in core.last_prompt["system"]


def test_emotion_contract_holds_with_chunked_recall(tmp_path):
    repo = JsonRepository(tmp_path / "s.json")
    core = _chunk_rag_core(repo, "owner", on=True)
    s = core.start_session()
    core.reply(_ALONG, s)
    state = core.reply("нагадай барвінок5", s)
    assert isinstance(state, EmotionState) and 0 <= state.intensity <= 1

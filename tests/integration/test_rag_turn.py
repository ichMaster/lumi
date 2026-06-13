"""Automatic per-turn RAG — the recall block injected into the reply (LUMI-070).

Each turn embeds the incoming message and injects the most relevant past lines as a
«Релевантні моменти минулого» block in the **volatile tail** (never the cached prefix).
Best-effort + non-blocking; off / below-floor / no-embedder → no block. Mock embedder,
no paid calls.
"""

from core.agent import Core
from core.embedder import MockEmbedder
from core.llm import MockLLMClient
from core.prompt import build_system_prompt
from state.local_store import JsonRepository

BLOCK = "# Релевантні моменти минулого"
_UNSET = object()  # distinguish "use a MockEmbedder" from an explicit embedder=None


def _core(tmp_path, *, rag=True, floor=0.0, embedder=_UNSET, store="s.json"):
    return Core(
        llm=MockLLMClient("ок"),
        repository=JsonRepository(tmp_path / store),
        canon="Ти — Лілі.",
        model="m",
        embedder=MockEmbedder() if embedder is _UNSET else embedder,
        recall_enabled=True,
        rag_enabled=rag,
        rag_floor=floor,
    )


# --- prompt placement (tail, never the cached prefix) -----------------------
def test_recall_block_lives_in_the_tail_not_the_cached_prefix():
    system, prefix = build_system_prompt("CANON", recall="— 2026-01-01 · ти: «каву»")
    assert BLOCK in system          # injected into the assembled prompt
    assert BLOCK not in prefix      # but NOT in the cacheable prefix (it changes per turn)
    assert system.startswith(prefix)  # the cache invariant still holds


# --- the turn injects the relevant past -------------------------------------
def test_reply_injects_the_relevant_past(tmp_path):
    core = _core(tmp_path)
    s = core.start_session()
    core.reply("я люблю каву вранці на світанку", s)   # indexed on write
    core.reply("розкажи мені ще про каву", s)           # the query shares «каву»

    system = core.last_prompt["system"]
    assert BLOCK in system
    assert "каву" in system.split(BLOCK, 1)[1]          # the past coffee line surfaced in the block
    assert BLOCK not in core.last_prompt["cache_prefix"]  # tail-only


def test_rag_disabled_injects_no_block(tmp_path):
    core = _core(tmp_path, rag=False)
    s = core.start_session()
    core.reply("я люблю каву", s)
    core.reply("розкажи про каву", s)
    assert core.rag_enabled is False
    assert BLOCK not in core.last_prompt["system"]


def test_no_hit_above_floor_injects_no_block(tmp_path):
    # A high floor → even a real match is below it → empty block.
    core = _core(tmp_path, floor=0.99)
    s = core.start_session()
    core.reply("я люблю каву", s)
    core.reply("розкажи про каву", s)
    assert BLOCK not in core.last_prompt["system"]


def test_no_embedder_means_no_rag(tmp_path):
    core = _core(tmp_path, embedder=None)
    assert core.rag_enabled is False  # RAG needs the recall infra (embedder + index)
    s = core.start_session()
    core.reply("привіт", s)
    assert BLOCK not in core.last_prompt["system"]


class _BoomEmbedder:
    dim = 8

    def embed(self, texts, *, is_query=False):
        raise RuntimeError("embedding down")


def test_retrieval_error_degrades_to_no_block_and_never_breaks_the_turn(tmp_path):
    core = _core(tmp_path, embedder=_BoomEmbedder())
    s = core.start_session()
    state = core.reply("привіт", s)          # the turn still completes…
    assert state.reply
    assert BLOCK not in core.last_prompt["system"]  # …with no block (graceful)


def test_an_old_out_of_window_line_resurfaces(tmp_path):
    # The point of RAG: a line long out of the verbatim window comes back when the topic returns.
    core = Core(
        llm=MockLLMClient("ок"), repository=JsonRepository(tmp_path / "s.json"),
        canon="Ти — Лілі.", model="m", embedder=MockEmbedder(),
        recall_enabled=True, rag_enabled=True, rag_floor=0.0,
        memory_window=2, compaction_batch=2,  # tiny window → old lines leave it fast
    )
    s = core.start_session()
    core.reply("пуер найкращий на третій заварці", s)   # the line we want back later
    for filler in ("як погода", "що нового", "розкажи жарт", "добраніч"):
        core.reply(filler, s)                            # push пуер out of the live window
    core.reply("нагадай про пуер", s)                    # topic returns
    block = core.last_prompt["system"].split(BLOCK, 1)[1] if BLOCK in core.last_prompt["system"] else ""
    assert "заварці" in block                            # the old пуер line resurfaced via RAG

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
    # A small window so the matched line leaves it (else dedup would suppress it — LUMI-071).
    core = Core(
        llm=MockLLMClient("ок"), repository=JsonRepository(tmp_path / "s.json"),
        canon="Ти — Лілі.", model="m", embedder=MockEmbedder(),
        recall_enabled=True, rag_enabled=True, rag_floor=0.0,
        memory_window=2, compaction_batch=2,
    )
    s = core.start_session()
    core.reply("я люблю каву вранці на світанку", s)     # indexed on write
    for filler in ("погода", "новини", "жарт"):           # push the coffee line out of the window
        core.reply(filler, s)
    core.reply("розкажи мені ще про каву", s)             # the query shares «каву»

    system = core.last_prompt["system"]
    assert BLOCK in system
    assert "кав" in system.split(BLOCK, 1)[1]             # the past coffee line surfaced in the block
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


# --- dedup against the window + bounds (LUMI-071) ---------------------------
def test_dedup_against_the_live_window(tmp_path):
    core = _core(tmp_path)  # rag on, floor 0
    s = core.start_session()
    core.reply("пуер найкращий на третій заварці", s)
    user_msg = next(m for m in core._repo.load_messages(s.id) if "заварці" in m.text)

    # The same line, with it IN the window → deduped (not repeated); with an empty window → injected.
    with_window = core._recall_block("розкажи про пуер заварці", live=[user_msg])
    without_window = core._recall_block("розкажи про пуер заварці", live=[])
    assert with_window is None or "заварці" not in with_window   # no double-context
    assert without_window and "заварці" in without_window         # otherwise it IS a valid hit


def test_block_respects_the_char_budget(tmp_path):
    core = Core(
        llm=MockLLMClient("ок"), repository=JsonRepository(tmp_path / "s.json"),
        canon="Ти — Лілі.", model="m", embedder=MockEmbedder(),
        recall_enabled=True, rag_enabled=True, rag_floor=0.0,
        rag_k=10, rag_max_chars=120,
    )
    s = core.start_session()
    for i in range(8):
        core.reply(f"пуер чай історія розповідь номер {i} про смак і аромат напою", s)
    block = core._recall_block("пуер чай історія смак", live=[])
    assert block is not None
    assert len(block) <= 120 + 80          # bounded by the budget (allow one trailing line)
    assert block.count("\n") + 1 < 10      # capped well below the 10 hits


def test_long_recalled_line_is_snippet_truncated(tmp_path):
    from core.agent import _RAG_SNIPPET_CHARS
    core = _core(tmp_path)
    s = core.start_session()
    core.reply("пуер " + "дуже довга історія про чай та аромат напою ".strip() * 30, s)
    block = core._recall_block("пуер історія чай аромат", live=[])
    assert block and "…" in block          # the long line is snippet-truncated
    assert all(len(ln) <= _RAG_SNIPPET_CHARS + 60 for ln in block.splitlines())


# --- context expansion (LUMI-072) -------------------------------------------
def test_hit_is_widened_to_a_neighbour_snippet_anchor_marked(tmp_path):
    core = _core(tmp_path)  # rag on, floor 0, W=2
    s = core.start_session()
    core.reply("привіт як ти", s)
    core.reply("розкажи мені про безсоння нічне", s)   # the line we'll hit
    core.reply("дякую на добраніч", s)
    block = core._recall_block("безсоння", live=[])
    assert block
    assert "← (matched)" in block                       # the anchor is marked
    assert "безсоння" in block                           # the matched line
    assert block.count("\n") >= 2                        # a multi-line moment, not a bare line


def test_overlapping_windows_merge_no_line_twice(tmp_path):
    core = _core(tmp_path)
    s = core.start_session()
    core.reply("пуер один історія", s)
    core.reply("пуер два історія", s)   # adjacent → ±W windows overlap
    core.reply("пуер три історія", s)
    block = core._recall_block("пуер історія", live=[])
    assert block.count("пуер один історія") == 1         # each line once (merged, not repeated)
    assert block.count("пуер два історія") == 1
    assert block.count("—", 0, len(block)) and block.count("\n— ") == 0  # ONE merged snippet header


def test_unresolved_hit_degrades_to_a_bare_anchor(tmp_path, monkeypatch):
    core = _core(tmp_path)
    s = core.start_session()
    core.reply("пуер найкращий на заварці", s)
    monkeypatch.setattr(core, "_position_of", lambda _mid: None)  # force the neighbour lookup to miss
    block = core._recall_block("пуер заварці", live=[])
    assert block and "← (matched)" in block              # still a marked anchor…
    assert "пуер" in block                               # …just without its neighbours


def test_rag_w_zero_gives_just_the_anchor(tmp_path):
    # floor>0 + a distinctive token so exactly ONE message matches (the others score 0).
    core = Core(
        llm=MockLLMClient("ок"), repository=JsonRepository(tmp_path / "s.json"),
        canon="Ти — Лілі.", model="m", embedder=MockEmbedder(),
        recall_enabled=True, rag_enabled=True, rag_floor=0.1, rag_w=0,
    )
    s = core.start_session()
    core.reply("привіт як справи", s)
    core.reply("розкажи про дирижабль над містом", s)   # only this shares «дирижабль»
    core.reply("добраніч друже", s)
    block = core._recall_block("дирижабль", live=[])
    # W=0 → exactly the one anchor line, no neighbours
    assert block and block.count("← (matched)") == 1
    assert "дирижабль" in block
    assert "добраніч" not in block and "привіт" not in block


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

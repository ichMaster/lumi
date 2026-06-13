"""Explicit semantic search — Core.recall + the /recall command (LUMI-064).

Embed the query → top-K over THIS user's vectors → dated, scored matches. Backfills a
cold store first; off / empty / error → [] (never raises); scoped to the active user.
Mock embedder + mock model — no network, no paid APIs.
"""

from core.agent import Core
from core.embedder import MockEmbedder
from core.llm import MockLLMClient
from state.local_store import JsonRepository
from tui.app import LumiApp


def _core(tmp_path, *, embedder=None, recall=True, reply="привіт", store="store.json"):
    return Core(
        llm=MockLLMClient(reply),
        repository=JsonRepository(tmp_path / store),
        canon="Ти — Лілі.",
        model="m",
        embedder=embedder,
        recall_enabled=recall,
    )


def test_recall_returns_dated_topk_scoped_to_user(tmp_path):
    core = _core(tmp_path, embedder=MockEmbedder())
    session = core.start_session()
    core.reply("я люблю каву вранці", session)
    core.reply("погода сьогодні похмура", session)

    # The mock is bag-of-words: share an exact token ("каву") to rank the coffee line first.
    hits = core.recall("каву")
    assert hits  # found something
    score, rec = hits[0]
    assert "каву" in rec.text                       # the coffee line ranks first
    assert score > 0.0                              # a real overlap, not a zero-vector tie
    assert rec.ts[:4].isdigit()                     # dated (ISO timestamp)
    assert rec.user_id == "owner"                   # scoped to the active user


def test_recall_honours_k(tmp_path):
    core = _core(tmp_path, embedder=MockEmbedder())
    session = core.start_session()
    for t in ("кава один", "кава два", "кава три"):
        core.reply(t, session)
    assert len(core.recall("кава", k=2)) == 2


def test_recall_empty_query_returns_empty(tmp_path):
    core = _core(tmp_path, embedder=MockEmbedder())
    session = core.start_session()
    core.reply("щось", session)
    assert core.recall("   ") == []


def test_recall_off_or_no_embedder_returns_empty(tmp_path):
    off = _core(tmp_path, embedder=MockEmbedder(), recall=False, store="a.json")
    s1 = off.start_session()
    off.reply("привіт", s1)
    assert off.recall("привіт") == []

    none = _core(tmp_path, embedder=None, recall=True, store="b.json")
    s2 = none.start_session()
    none.reply("привіт", s2)
    assert none.recall("привіт") == []


def test_recall_backfills_a_cold_store(tmp_path):
    # Messages written with recall OFF have no vectors; recall() must backfill then answer.
    store = tmp_path / "store.json"
    off = Core(llm=MockLLMClient("ок"), repository=JsonRepository(store),
               canon="Ти — Лілі.", model="m", embedder=MockEmbedder(), recall_enabled=False)
    s = off.start_session()
    off.reply("кава вранці", s)

    on = Core(llm=MockLLMClient("ок"), repository=JsonRepository(store),
              canon="Ти — Лілі.", model="m", embedder=MockEmbedder(), recall_enabled=True)
    assert not on._repo._vectors.get("owner")  # cold: nothing indexed yet
    hits = on.recall("кава")                    # backfills, then searches
    assert any("кава" in rec.text for _, rec in hits)


class _BoomEmbedder:
    dim = 8

    def embed(self, texts):
        raise RuntimeError("down")


def test_recall_never_raises_on_embed_error(tmp_path):
    core = _core(tmp_path, embedder=_BoomEmbedder())
    session = core.start_session()
    core.reply("привіт", session)  # index-on-write also fails silently
    assert core.recall("привіт") == []  # search degrades to empty, not an exception


# --- expanded snippets (LUMI-073) -------------------------------------------
def test_recall_moments_returns_expanded_dated_snippets(tmp_path):
    core = _core(tmp_path, embedder=MockEmbedder())
    s = core.start_session()
    core.reply("привіт як ти сьогодні", s)
    core.reply("розкажи мені про дирижабль у небі", s)   # the line we'll search
    core.reply("дякую дуже цікаво", s)

    moments = core.recall_moments("дирижабль")
    assert moments
    joined = "\n".join(moments)
    assert "← (matched, 0." in joined          # the anchor is marked WITH its score
    assert "дирижабль" in joined               # the matched line
    assert joined.lstrip().startswith("—")     # dated snippet header (a moment, not a bare line)
    assert "  " in joined                      # indented dialogue lines (neighbours)


def test_recall_moments_empty_or_off(tmp_path):
    on = _core(tmp_path, embedder=MockEmbedder(), store="x.json")
    assert on.recall_moments("   ") == []      # empty query
    off = _core(tmp_path, embedder=MockEmbedder(), recall=False, store="y.json")
    s = off.start_session()
    off.reply("привіт", s)
    assert off.recall_moments("привіт") == []  # recall off


# --- the /recall TUI command -------------------------------------------------
async def _submit(pilot, app, text):
    app.query_one("#prompt").text = text
    await pilot.press("enter")
    for _ in range(20):
        await pilot.pause()
        if app.transcript:
            break


async def test_recall_command_renders_expanded_snippets(tmp_path):
    core = _core(tmp_path, embedder=MockEmbedder())
    app = LumiApp(core)
    async with app.run_test() as pilot:
        await _submit(pilot, app, "я люблю каву вранці")   # a turn to index
        app.transcript.clear()
        await _submit(pilot, app, "/recall кава")
        joined = "\n".join(app.transcript)
        assert "Згадую про «кава»" in joined
        assert "ти:" in joined and "кава" in joined        # who + the text
        assert "← (matched" in joined                      # the anchor is marked (expanded snippet)


async def test_recall_command_empty_query_prompts(tmp_path):
    app = LumiApp(_core(tmp_path, embedder=MockEmbedder()))
    async with app.run_test() as pilot:
        await _submit(pilot, app, "/recall")
        assert any("Що згадати" in line for line in app.transcript)


async def test_recall_command_says_when_disabled(tmp_path):
    app = LumiApp(_core(tmp_path, embedder=MockEmbedder(), recall=False))
    async with app.run_test() as pilot:
        await _submit(pilot, app, "/recall кава")
        assert any("вимкнено" in line for line in app.transcript)

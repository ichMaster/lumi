"""v0.35 LUMI-139 — gist the conversation tier: keep the last N sessions verbatim, gist the rest.

``LUMI_SESSION_DETAIL_N`` = 0 (default) keeps the whole window verbatim (byte-identical to pre-v0.35); a small
N keeps only the most recent N sessions' detailed `summary` and renders the older window as a dated one-line
`gist` index. Mock model — no paid calls; per-user.
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from core.memory import session_gist
from core.repository import ShortSummary
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 6, 8, 12, 0, tzinfo=UTC))


def _core(tmp_path, *, session_detail_n=0, user="owner"):
    return Core(
        llm=MockLLMClient("ок"), repository=JsonRepository(tmp_path / "s.json"),
        canon="C", model="m", clock=_CLK, user_id=user, mood_enabled=False, biorhythms_enabled=False,
        cycle_enabled=False, thoughts_enabled=False,
        session_days=2, session_detail_n=session_detail_n,
    )


def _seed_three_sessions(core):
    # oldest-first; each session has a DETAILED summary + a one-line gist
    core._repo.add_summary(ShortSummary("owner", "s1", "ДЕТАЛЬНО про каву й маршрут", "стисло: кава", "2026-06-07T09:00:00+00:00"))
    core._repo.add_summary(ShortSummary("owner", "s2", "ДЕТАЛЬНО про книгу й парк", "стисло: книга", "2026-06-08T10:00:00+00:00"))
    core._repo.add_summary(ShortSummary("owner", "s3", "ДЕТАЛЬНО про сон і роботу", "стисло: сон", "2026-06-08T20:00:00+00:00"))


# --- the helper -----------------------------------------------------------
def test_session_gist_uses_the_gist_then_falls_back_to_a_one_liner():
    assert session_gist("стисло", "довгий детальний підсумок") == "стисло"        # the v0.9 gist wins
    assert session_gist("", "перший рядок\nдругий") == "перший рядок другий"  # no gist → one-line fallback
    assert len(session_gist("  ", "x" * 200)) == 120                              # truncated, never empty


# --- the tier -------------------------------------------------------------
def test_off_keeps_the_whole_window_verbatim(tmp_path):
    core = _core(tmp_path, session_detail_n=0)  # default → all verbatim (byte-identical)
    _seed_three_sessions(core)
    core.reply("привіт", core.start_session())
    system = core.last_prompt["system"]
    assert all(d in system for d in ("ДЕТАЛЬНО про каву", "ДЕТАЛЬНО про книгу", "ДЕТАЛЬНО про сон"))


def test_keeps_last_n_verbatim_and_gists_the_older_window(tmp_path):
    core = _core(tmp_path, session_detail_n=1)  # only the most recent conversation stays verbatim
    _seed_three_sessions(core)
    core.reply("привіт", core.start_session())
    system = core.last_prompt["system"]
    assert "ДЕТАЛЬНО про сон" in system            # the live thread (most recent) stays verbatim
    assert "ДЕТАЛЬНО про каву" not in system        # older sessions → gisted (detail not injected)
    assert "ДЕТАЛЬНО про книгу" not in system
    assert "стисло: кава" in system and "стисло: книга" in system  # their dated one-line gist instead
    assert "[2026-06-07]" in system                 # the gisted entry keeps its date (messages_on key)


def test_gisted_window_is_per_user_isolated(tmp_path):
    core = _core(tmp_path, session_detail_n=1)
    _seed_three_sessions(core)
    core.reply("привіт", core.start_session())
    system = core.last_prompt["system"]
    # bob has no sessions → none of owner's gists or details leak into bob's view
    bob = _core(tmp_path, session_detail_n=1, user="bob")
    bob.reply("hi", bob.start_session())
    bob_system = bob.last_prompt["system"]
    assert "стисло: кава" in system and "стисло: кава" not in bob_system


# --- v0.35 LUMI-140: a gisted session's detail stays reachable (push + pull) ---
def test_auto_rag_reaches_an_older_session_with_gisting_on(tmp_path):
    # the PUSH path: auto-RAG dedup keys off the live WINDOW (raw messages), not the summary tier — so an
    # older session's line resurfaces even with the conversation tier gisted. (LUMI-140 needs no dedup change.)
    from core.embedder import MockEmbedder
    core = Core(
        llm=MockLLMClient("ок"), repository=JsonRepository(tmp_path / "s.json"),
        canon="C", model="m", clock=_CLK, embedder=MockEmbedder(),
        recall_enabled=True, rag_enabled=True, rag_floor=0.0,
        memory_window=4, session_detail_n=1,  # gisting ON
        mood_enabled=False, biorhythms_enabled=False, cycle_enabled=False, thoughts_enabled=False,
    )
    a = core.start_session()
    core.reply("я люблю каву вранці на світанку", a)   # indexed in session A
    b = core.start_session()                             # a new session → A is now an older session
    core.reply("розкажи мені ще про каву", b)            # the query in session B shares «каву»
    system = core.last_prompt["system"]
    assert "# Релевантні моменти минулого" in system and "каву вранці" in system  # A's line resurfaced


def test_gisted_session_messages_are_pullable_by_date(tmp_path):
    # the PULL path: by-date retrieval reads raw messages from the store, independent of the gisted tier.
    core = _core(tmp_path, session_detail_n=1)
    core.reply("деталь про комети у нашій розмові", core.start_session())
    msgs = core._messages_in_range("2026-06-08", "2026-06-08")  # messages_on / messages_between back this
    assert any("комети" in m.text for m in msgs)


def test_verbatim_session_lines_are_not_re_surfaced(tmp_path):
    # no double-injection: a session shown VERBATIM in the conversation tier is in the auto-RAG dedup, so its
    # raw line is not also surfaced — while a gisted older session's line still would be (the test above).
    from core.embedder import MockEmbedder
    core = Core(
        llm=MockLLMClient("ок"), repository=JsonRepository(tmp_path / "s.json"),
        canon="C", model="m", clock=_CLK, embedder=MockEmbedder(),
        recall_enabled=True, rag_enabled=True, rag_floor=0.0,
        session_days=2, session_detail_n=1,  # gisting ON → the last-1 session is verbatim
        mood_enabled=False, biorhythms_enabled=False, cycle_enabled=False, thoughts_enabled=False,
    )
    a = core.start_session()
    core.reply("я люблю каву вранці на світанку", a)   # indexed in session A
    # mark A as a finished session WITH a summary → it is the verbatim (last-1) tier this turn
    core._repo.add_summary(ShortSummary("owner", a.id, "детальний підсумок про каву", "гіст А", "2026-06-08T11:00:00+00:00"))
    b = core.start_session()
    core.reply("розкажи мені ще про каву", b)
    system = core.last_prompt["system"]
    assert "детальний підсумок про каву" in system  # A's summary is shown verbatim in the tier
    assert "каву вранці" not in system               # …so its raw line is deduped, not re-surfaced

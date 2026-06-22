"""v0.35 — the conversation tier: two orthogonal knobs.

``LUMI_SESSION_DETAIL_N`` — how many of the most-recent sessions to add (None/unset = all; 0 = none; N = last N).
``LUMI_SESSION_FORMAT``   — the form each added session takes: ``summary`` (full) or ``gist`` (one line).
Default (None + ``summary``) = all sessions, full → unchanged from pre-v0.35. Mock model — no paid calls; per-user.
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


def _core(tmp_path, *, session_detail_n=None, session_format="summary", user="owner"):
    return Core(
        llm=MockLLMClient("ок"), repository=JsonRepository(tmp_path / "s.json"),
        canon="C", model="m", clock=_CLK, user_id=user, mood_enabled=False, biorhythms_enabled=False,
        cycle_enabled=False, thoughts_enabled=False,
        session_days=2, session_detail_n=session_detail_n, session_format=session_format,
    )


def _seed_three(core):
    # oldest-first; each session has a DETAILED summary + a one-line gist
    core._repo.add_summary(ShortSummary("owner", "s1", "ДЕТАЛЬНО про каву", "стисло: кава", "2026-06-07T09:00:00+00:00"))
    core._repo.add_summary(ShortSummary("owner", "s2", "ДЕТАЛЬНО про книгу", "стисло: книга", "2026-06-08T10:00:00+00:00"))
    core._repo.add_summary(ShortSummary("owner", "s3", "ДЕТАЛЬНО про сон", "стисло: сон", "2026-06-08T20:00:00+00:00"))


def _tier(core):
    core.reply("привіт", core.start_session())
    return core.last_prompt["system"]


# --- the helper -----------------------------------------------------------
def test_session_gist_uses_the_gist_then_falls_back_to_a_one_liner():
    assert session_gist("стисло", "довгий детальний підсумок") == "стисло"        # the v0.9 gist wins
    assert session_gist("", "перший рядок\nдругий") == "перший рядок другий"        # no gist → one-line fallback
    assert len(session_gist("  ", "x" * 200)) == 120                              # truncated, never empty


# --- count: LUMI_SESSION_DETAIL_N -----------------------------------------
def test_default_adds_all_sessions_as_summaries(tmp_path):
    core = _core(tmp_path)  # None + summary → all, full (byte-identical to pre-v0.35)
    _seed_three(core)
    system = _tier(core)
    assert all(d in system for d in ("ДЕТАЛЬНО про каву", "ДЕТАЛЬНО про книгу", "ДЕТАЛЬНО про сон"))


def test_zero_adds_no_sessions(tmp_path):
    core = _core(tmp_path, session_detail_n=0)
    _seed_three(core)
    system = _tier(core)
    assert "## Останні розмови" not in system  # the whole session tier is dropped
    assert "ДЕТАЛЬНО про сон" not in system and "стисло: сон" not in system


def test_last_n_caps_the_count(tmp_path):
    core = _core(tmp_path, session_detail_n=2)  # only the last 2 sessions
    _seed_three(core)
    system = _tier(core)
    assert "ДЕТАЛЬНО про книгу" in system and "ДЕТАЛЬНО про сон" in system  # the last 2
    assert "ДЕТАЛЬНО про каву" not in system and "стисло: кава" not in system  # the oldest dropped entirely


def test_n_larger_than_window_adds_all(tmp_path):
    core = _core(tmp_path, session_detail_n=100)  # > 3 sessions → all
    _seed_three(core)
    system = _tier(core)
    assert all(d in system for d in ("ДЕТАЛЬНО про каву", "ДЕТАЛЬНО про книгу", "ДЕТАЛЬНО про сон"))


# --- format: LUMI_SESSION_FORMAT ------------------------------------------
def test_format_gist_renders_added_sessions_as_one_line_gists(tmp_path):
    core = _core(tmp_path, session_format="gist")  # all sessions, but each as a one-line gist
    _seed_three(core)
    system = _tier(core)
    assert all(g in system for g in ("стисло: кава", "стисло: книга", "стисло: сон"))
    assert "ДЕТАЛЬНО" not in system  # no full summaries


def test_count_and_format_compose(tmp_path):
    core = _core(tmp_path, session_detail_n=1, session_format="gist")  # the last 1, as a gist
    _seed_three(core)
    system = _tier(core)
    assert "стисло: сон" in system                                           # the single most-recent, gisted
    assert "стисло: кава" not in system and "стисло: книга" not in system    # older dropped
    assert "ДЕТАЛЬНО" not in system


def test_per_user_isolated(tmp_path):
    core = _core(tmp_path, session_format="gist")
    _seed_three(core)
    system = _tier(core)
    bob_system = _tier(_core(tmp_path, session_format="gist", user="bob"))  # bob has no sessions
    assert "стисло: кава" in system and "стисло: кава" not in bob_system


# --- reconstruction: a gisted session's detail stays reachable ------------
def test_gisted_session_detail_is_reachable_push_and_pull(tmp_path):
    from core.embedder import MockEmbedder
    core = Core(
        llm=MockLLMClient("ок"), repository=JsonRepository(tmp_path / "s.json"),
        canon="C", model="m", clock=_CLK, embedder=MockEmbedder(),
        recall_enabled=True, rag_enabled=True, rag_floor=0.0,
        session_days=2, session_format="gist",  # sessions shown as gists
        mood_enabled=False, biorhythms_enabled=False, cycle_enabled=False, thoughts_enabled=False,
    )
    a = core.start_session()
    core.reply("я люблю каву вранці на світанку", a)   # indexed in session A
    b = core.start_session()
    core.reply("розкажи мені ще про каву", b)            # the query in session B shares «каву»
    system = core.last_prompt["system"]
    assert "каву вранці" in system  # PUSH: auto-RAG surfaces the older line (dedup keys off the live window)
    msgs = core._messages_in_range("2026-06-08", "2026-06-08")  # PULL: by-date reads raw messages
    assert any("каву вранці" in m.text for m in msgs)

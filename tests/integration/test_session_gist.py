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

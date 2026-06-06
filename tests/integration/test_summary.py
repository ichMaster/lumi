"""End-of-session summarization (LUMI-009)."""

from core.agent import Core
from core.llm import MockLLMClient
from core.repository import ShortSummary
from state.local_store import JsonRepository


def _core(tmp_path, llm, user_id="owner"):
    return Core(
        llm=llm,
        repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.",
        model="m",
        user_id=user_id,
    )


def test_end_session_writes_a_short_summary(tmp_path):
    # First reply is the turn; second is the summarization call.
    llm = MockLLMClient(["вітаю", "Користувач привітався; знайомство."])
    core = _core(tmp_path, llm)
    session = core.start_session()
    core.reply("привіт", session)

    summary = core.end_session(session)
    assert isinstance(summary, ShortSummary)
    assert summary.user_id == "owner"
    assert summary.session_id == session.id
    assert summary.summary == "Користувач привітався; знайомство."

    recent = core._repo.recent_summaries("owner")
    assert [s.summary for s in recent] == ["Користувач привітався; знайомство."]


def test_empty_session_produces_no_summary(tmp_path):
    core = _core(tmp_path, MockLLMClient("nope"))
    session = core.start_session()
    assert core.end_session(session) is None
    assert core._repo.recent_summaries("owner") == []


def test_model_failure_writes_no_summary_and_does_not_raise(tmp_path):
    calls = {"n": 0}

    def flaky(system, messages, model):
        calls["n"] += 1
        if calls["n"] == 1:
            return "вітаю"  # the turn succeeds
        raise RuntimeError("summarizer down")  # the summary call fails

    core = _core(tmp_path, MockLLMClient(flaky))
    session = core.start_session()
    core.reply("привіт", session)

    assert core.end_session(session) is None  # no raise
    assert core._repo.recent_summaries("owner") == []
    # The session is still marked ended.
    assert core._repo.get_session(session.id).ended_at is not None


def test_summaries_are_isolated_by_user(tmp_path):
    repo = JsonRepository(tmp_path / "store.json")
    repo.add_summary(ShortSummary("alice", "s1", "Alice's gist", "2026-06-06T10:00:00+00:00"))
    assert [s.summary for s in repo.recent_summaries("alice")] == ["Alice's gist"]
    assert repo.recent_summaries("bob") == []  # isolation invariant


def test_recent_summaries_caps_at_limit(tmp_path):
    repo = JsonRepository(tmp_path / "store.json")
    for i in range(7):
        repo.add_summary(ShortSummary("owner", f"s{i}", f"gist {i}", "2026-06-06T10:00:00+00:00"))
    recent = repo.recent_summaries("owner", limit=3)
    assert [s.summary for s in recent] == ["gist 4", "gist 5", "gist 6"]

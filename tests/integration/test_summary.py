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
    repo.add_summary(ShortSummary("alice", "s1", "Alice's gist", "g", "2026-06-06T10:00:00+00:00"))
    assert [s.summary for s in repo.recent_summaries("alice")] == ["Alice's gist"]
    assert repo.recent_summaries("bob") == []  # isolation invariant


def test_recent_summaries_caps_at_limit(tmp_path):
    repo = JsonRepository(tmp_path / "store.json")
    for i in range(7):
        repo.add_summary(ShortSummary("owner", f"s{i}", f"gist {i}", f"g{i}", "2026-06-06T10:00:00+00:00"))
    recent = repo.recent_summaries("owner", limit=3)
    assert [s.summary for s in recent] == ["gist 4", "gist 5", "gist 6"]


# --- v0.9 (LUMI-034): two-tier summary (detailed + gist) + migration -----
from core.memory import parse_summary  # noqa: E402


def test_parse_summary_splits_detailed_and_gist():
    detailed, gist = parse_summary("Детальний підсумок про гори й каву.\n\nСТИСЛО: говорили про гори.")
    assert detailed == "Детальний підсумок про гори й каву."
    assert gist == "говорили про гори."


def test_parse_summary_falls_back_to_first_sentence_when_no_marker():
    detailed, gist = parse_summary("Перше речення тут. Друге речення.")
    assert detailed == "Перше речення тут. Друге речення."  # the whole text stays detailed
    assert gist == "Перше речення тут."  # first sentence → fallback gist


def test_end_session_writes_both_tiers(tmp_path):
    llm = MockLLMClient(["вітаю", "Детальний підсумок розмови.\nСТИСЛО: коротка суть."])
    core = _core(tmp_path, llm)
    session = core.start_session()
    core.reply("привіт", session)
    summary = core.end_session(session)
    assert summary.summary == "Детальний підсумок розмови."  # detailed tier
    assert summary.gist == "коротка суть."  # gist tier (one call)


def test_old_summary_without_gist_loads_migrated(tmp_path):
    import json

    p = tmp_path / "store.json"
    p.write_text(
        json.dumps(
            {  # an old-shape record — no `gist`
                "summaries": {
                    "owner": [
                        {
                            "user_id": "owner",
                            "session_id": "s1",
                            "summary": "стара памʼять",
                            "ts": "2026-06-06T10:00:00+00:00",
                        }
                    ]
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    recent = JsonRepository(p).recent_summaries("owner")
    assert len(recent) == 1
    assert recent[0].summary == "стара памʼять" and recent[0].gist == ""  # migrated


# --- v0.9 (LUMI-035): summaries within the last D local days -------------
def test_summaries_since_returns_on_or_after_the_date(tmp_path):
    repo = JsonRepository(tmp_path / "store.json")
    repo.add_summary(ShortSummary("owner", "old", "стара", "g", "2026-06-01T10:00:00+00:00"))
    repo.add_summary(ShortSummary("owner", "mid", "середня", "g", "2026-06-05T23:00:00+00:00"))
    repo.add_summary(ShortSummary("owner", "new", "нова", "g", "2026-06-08T08:00:00+00:00"))
    since = repo.summaries_since("owner", "2026-06-05")
    assert [s.session_id for s in since] == ["mid", "new"]  # on/after, newest last
    assert repo.summaries_since("owner", "2026-06-09") == []  # none in range


def test_summaries_since_is_isolated_by_user(tmp_path):
    repo = JsonRepository(tmp_path / "store.json")
    repo.add_summary(ShortSummary("alice", "a1", "A", "g", "2026-06-08T10:00:00+00:00"))
    assert len(repo.summaries_since("alice", "2026-06-01")) == 1
    assert repo.summaries_since("bob", "2026-06-01") == []  # B never sees A's records


# --- v0.9 (LUMI-036): two-tier short-memory injection --------------------
from datetime import UTC, datetime  # noqa: E402

from core.clock import fixed_clock  # noqa: E402


def _ss(sid, detail, gist, ts):
    return ShortSummary("owner", sid, detail, gist, ts)


def test_three_date_based_tiers_injection(tmp_path):
    from core.repository import DaySummary, WeekSummary

    # Clock = Mon 2026-06-08. Tier windows: sessions ≤2d (≥Jun 6), days ≤7d (≥Jun 1), weeks ≤14d.
    repo = JsonRepository(tmp_path / "s.json")
    for i in range(3):  # tier 1 (sessions, detailed): within the last 2 days
        repo.add_summary(_ss(f"r{i}", f"RDETAIL{i}", f"RGIST{i}", "2026-06-07T10:00:00+00:00"))
    # tier 2 (days): a digest in the 7-day window + one beyond it
    repo.set_day_summary(DaySummary("owner", "2026-06-03", "День теплий.\nГоворили про гори.",
                                    2, "2026-06-03T23:00:00+00:00"))
    repo.set_day_summary(DaySummary("owner", "2026-05-20", "Старий день.", 1, "2026-05-20T23:00:00+00:00"))
    # tier 3 (weeks): a week digest in the 14-day window + one beyond it
    repo.set_week_summary(WeekSummary("owner", "2026-06-01", "Тиждень про гори й каву.", 9, "t"))
    repo.set_week_summary(WeekSummary("owner", "2026-05-04", "Дуже старий тиждень.", 3, "t"))

    core = Core(
        llm=MockLLMClient(states={"reply": "ок", "emotion": "calm", "intensity": 0.5}),
        repository=repo, canon="C", model="m",
        clock=fixed_clock(datetime(2026, 6, 8, 12, 0, tzinfo=UTC)), mood_enabled=False,
    )
    sysp = core._system_prompt(core.start_session())

    # Grouped under one markdown memory section, coarse → fine.
    assert "# Памʼять про цю людину" in sysp
    # Tier 1 — sessions in the last 2 days (detailed), dated.
    assert "## Останні розмови (детально)" in sysp
    for i in range(3):
        assert f"[2026-06-07] RDETAIL{i}" in sysp
    # Tier 2 — day digest in the 7-day window; the older one excluded.
    assert "## Останні дні" in sysp
    assert "[2026-06-03] День теплий. Говорили про гори." in sysp
    assert "Старий день." not in sysp
    # Tier 3 — week digest in the 14-day window; the older one excluded.
    assert "## Останні тижні" in sysp
    assert "[тиждень з 2026-06-01] Тиждень про гори й каву." in sysp
    assert "Дуже старий тиждень." not in sysp
    # Order: weeks → days → sessions (coarse to fine).
    assert sysp.index("Останні тижні") < sysp.index("Останні дні") < sysp.index("Останні розмови")


def test_no_tiers_when_no_summaries(tmp_path):
    repo = JsonRepository(tmp_path / "s.json")
    core = Core(
        llm=MockLLMClient(states={"reply": "ок", "emotion": "calm", "intensity": 0.5}),
        repository=repo, canon="C", model="m",
        clock=fixed_clock(datetime(2026, 6, 8, 12, 0, tzinfo=UTC)), mood_enabled=False,
    )
    sysp = core._system_prompt(core.start_session())
    assert "# Памʼять про цю людину" not in sysp  # no memory at all → no section

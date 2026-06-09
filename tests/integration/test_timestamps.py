"""v0.4 LUMI-019: per-message timestamps + dated summaries in the prompt."""

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from core.repository import ShortSummary
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 6, 5, 9, 0, tzinfo=UTC))


def _core(tmp_path, repo=None):
    return Core(
        llm=MockLLMClient("ok"),
        repository=repo or JsonRepository(tmp_path / "s.json"),
        canon="Ти — Лілі.",
        model="m",
        clock=_CLK,
    )


def test_every_message_carries_a_timestamp(tmp_path):
    core = _core(tmp_path)
    session = core.start_session()
    core.reply("привіт", session)  # turn 1 (persists stamped messages)
    core.reply("ще", session)      # turn 2 → history is replayed with stamps
    msgs = core.last_prompt["messages"]
    assert msgs and all(m["content"].startswith("[2026-06-05 09:00] ") for m in msgs)


def test_recalled_summaries_are_dated_in_the_prompt(tmp_path):
    repo = JsonRepository(tmp_path / "s.json")
    repo.add_summary(
        ShortSummary(
            user_id="owner",
            session_id="recent",
            summary="Говорили про гори.",
            gist="гори",
            ts="2026-06-04T10:00:00+00:00",  # within the 2-day session-detail window (clock = Jun 5)
        )
    )
    core = _core(tmp_path, repo=repo)
    core.reply("привіт", core.start_session())
    assert "[2026-06-04] Говорили про гори." in core.last_prompt["system"]

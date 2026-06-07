"""Response stats, running totals, and the end-of-session thinking fix."""

from core.agent import Core
from core.llm import MockLLMClient, ResponseStats
from core.memory import FACTS_SYSTEM, SUMMARY_SYSTEM
from state.local_store import JsonRepository


def _core(tmp_path, llm):
    return Core(llm=llm, repository=JsonRepository(tmp_path / "s.json"),
                canon="Ти — Лілі.", model="claude-opus-4-8")


def test_reply_records_stats_and_totals(tmp_path):
    core = _core(tmp_path, MockLLMClient("ok"))
    session = core.start_session()
    core.reply("привіт", session)

    assert isinstance(core.last_stats, ResponseStats)
    assert core.last_stats.model == "claude-opus-4-8"
    assert core.totals.turns == 1

    core.reply("ще", session)
    assert core.totals.turns == 2


class _ThinkingRecorder:
    """A fake LLM that records whether thinking was on during each call."""

    def __init__(self):
        self._thinking = True  # thinking starts ON
        self.last_thinking = None
        self.last_stats = None
        self.thinking_per_call: list[bool] = []

    def reply(self, system, messages, model):
        self.thinking_per_call.append(self._thinking)
        self.last_stats = ResponseStats(model=model, latency_ms=0)
        # During end-of-session housekeeping the system is the summary/facts prompt.
        return "підсумок" if system in (SUMMARY_SYSTEM, FACTS_SYSTEM) else "відповідь"

    def reply_structured(self, system, messages, model):
        # The user turn (v0.3) goes through the structured path.
        self.thinking_per_call.append(self._thinking)
        self.last_stats = ResponseStats(model=model, latency_ms=0)
        return {"reply": "відповідь", "emotion": "calm", "intensity": 0.5}


def test_end_session_disables_thinking_for_housekeeping(tmp_path):
    rec = _ThinkingRecorder()
    core = _core(tmp_path, rec)
    session = core.start_session()
    core.reply("привіт", session)  # call 0: user turn (thinking ON)
    core.end_session(session)      # calls 1,2: summary + facts (thinking OFF)

    # The user turn ran with thinking on; the two housekeeping calls ran with it off.
    assert rec.thinking_per_call[0] is True
    assert rec.thinking_per_call[1] is False
    assert rec.thinking_per_call[2] is False
    # ...and thinking is restored afterward (so it isn't permanently disabled).
    assert rec._thinking is True

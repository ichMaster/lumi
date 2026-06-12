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


def test_stats_line_shows_cache_read_and_write(tmp_path):
    # v0.15 (LUMI-068): the status line surfaces prompt-cache read/write.
    from tui.app import LumiApp
    core = _core(tmp_path, MockLLMClient("ok"))
    core.last_stats = ResponseStats(
        model="m", latency_ms=100, input_tokens=500, output_tokens=50,
        cache_read_tokens=9800, cache_write_tokens=2400,
    )
    core.totals.turns = 1
    text = LumiApp(core)._stats_text()
    assert "cache" in text and "↩" in text   # prefix served from the cache
    assert "wrote" in text and "↑" in text    # prefix (re)written this turn


def test_prompt_dump_tokens_line_shows_in_out_and_cache(tmp_path):
    # v0.15: the /prompt dump carries the last turn's token cost (in/out + cache).
    from tui.app import LumiApp
    core = _core(tmp_path, MockLLMClient("ok"))
    core.last_stats = ResponseStats(
        model="m", latency_ms=1200, input_tokens=16000, output_tokens=180,
        cache_read_tokens=14000, cache_write_tokens=0,
    )
    line = LumiApp(core)._last_tokens_line()
    assert line.startswith("[TOKENS]")
    assert "in" in line and "out" in line and "↩" in line  # in/out tokens + cache read


class _ThinkingRecorder:
    """A fake LLM that records whether thinking was on during each call."""

    def __init__(self):
        self._thinking = True  # thinking starts ON
        self.last_thinking = None
        self.last_stats = None
        self.thinking_per_call: list[bool] = []

    def reply(self, system, messages, model, cache_prefix=None):
        self.thinking_per_call.append(self._thinking)
        self.last_stats = ResponseStats(model=model, latency_ms=0)
        # During end-of-session housekeeping the system is the summary/facts prompt.
        return "підсумок" if system in (SUMMARY_SYSTEM, FACTS_SYSTEM) else "відповідь"

    def reply_structured(self, system, messages, model, cache_prefix=None):
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

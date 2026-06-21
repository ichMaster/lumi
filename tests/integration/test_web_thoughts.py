"""v0.33 LUMI-133 — web-thoughts (%search / %events): the v0.27 web_lookup tool in the think path.

Off (master / LUMI_WEB_LOOKUP / LUMI_THOUGHT_WEB) → absent. Mock model + stub GeminiSearch — no paid calls.
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 6, 21, 12, 0, tzinfo=UTC))


def _stub(answer="Lviv hosts a festival this weekend."):
    seen: list[tuple[str, str]] = []

    def search(query: str, *, today: str) -> str:
        seen.append((query, today))
        return answer

    search.seen = seen  # type: ignore[attr-defined]
    return search


def _core(tmp_path, mock, *, master=True, web_tool=True, thought_web=True, search=None):
    return Core(
        llm=mock, repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.", model="m", clock=_CLK, user_id="owner",
        mood_enabled=False, closeness_enabled=False,
        thoughts_enabled=True, thought_tools_enabled=master, thought_web=thought_web,
        web_lookup_enabled=web_tool, web_search=search or _stub(),
    )


def test_search_runs_the_web_loop_and_records_a_thought(tmp_path):
    seen = _stub()
    mock = MockLLMClient("Глянула — цими вихідними фестиваль.\nЕМОЦІЯ: joy",
                         tool_script=[("web_lookup", {"query": "Львів події"})])
    core = _core(tmp_path, mock, search=seen)
    out = core.run_directive("%search", core.start_session())
    assert out.is_directive and out.thought.kind == "search"
    assert [c[0] for c in mock.tool_calls] == ["web_lookup"]       # the web loop ran
    assert seen.seen and seen.seen[0][1] == "2026-06-21"           # date-anchored to today


def test_events_is_also_a_web_directive(tmp_path):
    mock = MockLLMClient("Що там попереду.\nЕМОЦІЯ: calm",
                         tool_script=[("web_lookup", {"query": "upcoming"})])
    out = _core(tmp_path, mock).run_directive("%events", _core(tmp_path, mock).start_session())
    assert out.is_directive and out.thought.kind == "events"


def test_web_thoughts_absent_unless_all_gates_on(tmp_path):
    def mk():
        return MockLLMClient("x\nЕМОЦІЯ: calm", tool_script=[("web_lookup", {"query": "x"})])
    assert _core(tmp_path, mk(), master=False).run_directive(
        "%search", _core(tmp_path, mk(), master=False).start_session()).is_directive is False
    assert _core(tmp_path, mk(), web_tool=False).run_directive(
        "%search", _core(tmp_path, mk(), web_tool=False).start_session()).is_directive is False
    assert _core(tmp_path, mk(), thought_web=False).run_directive(
        "%events", _core(tmp_path, mk(), thought_web=False).start_session()).is_directive is False

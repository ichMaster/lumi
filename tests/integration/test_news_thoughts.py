"""v0.33 LUMI-131 — news-thoughts (%catchup / %brief): the v0.25 Guardian news tools in the think path.

Off (master / LUMI_NEWS_TOOL / LUMI_THOUGHT_NEWS) → absent. Mock model + mock transport — no network/key.
"""
from __future__ import annotations

import json
import urllib.parse
from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 6, 21, 12, 0, tzinfo=UTC))

_SEARCH = {"response": {"status": "ok", "results": [
    {"id": "world/2026/jun/21/a", "webTitle": "A", "webUrl": "https://www.theguardian.com/world/a",
     "sectionName": "World news", "webPublicationDate": "2026-06-21T08:00:00Z",
     "fields": {"headline": "A happens", "trailText": "A summary", "byline": "Reporter A"}}]}}
_ARTICLE = {"response": {"status": "ok", "content": {
    "id": "world/2026/jun/21/a", "webTitle": "A happens", "webUrl": "https://www.theguardian.com/world/a",
    "fields": {"bodyText": "The full English body of article A.", "byline": "Reporter A"}}}}


def _fake_http():
    def http_get(url: str) -> str:
        path = urllib.parse.urlparse(url).path
        return json.dumps(_SEARCH if path.endswith("/search") else _ARTICLE)
    return http_get


def _core(tmp_path, mock, *, master=True, news_tool=True, thought_news=True):
    return Core(
        llm=mock, repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.", model="m", clock=_CLK, user_id="owner",
        mood_enabled=False, closeness_enabled=False,
        thoughts_enabled=True, thought_tools_enabled=master, thought_news=thought_news,
        news_enabled=news_tool, news_http_get=_fake_http(),
    )


def test_catchup_runs_the_news_loop_and_records_a_thought(tmp_path):
    mock = MockLLMClient("У світі сьогодні неспокійно (Guardian).\nЕМОЦІЯ: thoughtful",
                         tool_script=[("news_search", {"query": "world"})])
    core = _core(tmp_path, mock)
    out = core.run_directive("%catchup", core.start_session())
    assert out.is_directive and out.thought.kind == "catchup"
    assert [c[0] for c in mock.tool_calls] == ["news_search"]  # the news loop ran


def test_brief_is_also_a_news_directive(tmp_path):
    mock = MockLLMClient("Кілька новин, коротко.\nЕМОЦІЯ: calm",
                         tool_script=[("news_search", {"query": "tech"})])
    out = _core(tmp_path, mock).run_directive("%brief", _core(tmp_path, mock).start_session())
    assert out.is_directive and out.thought.kind == "brief"


def test_news_thoughts_absent_unless_all_gates_on(tmp_path):
    def mk():
        return MockLLMClient("x\nЕМОЦІЯ: calm", tool_script=[("news_search", {"query": "x"})])
    assert _core(tmp_path, mk(), master=False).run_directive(
        "%catchup", _core(tmp_path, mk(), master=False).start_session()).is_directive is False
    assert _core(tmp_path, mk(), news_tool=False).run_directive(
        "%catchup", _core(tmp_path, mk(), news_tool=False).start_session()).is_directive is False
    assert _core(tmp_path, mk(), thought_news=False).run_directive(
        "%brief", _core(tmp_path, mk(), thought_news=False).start_session()).is_directive is False

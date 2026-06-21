"""v0.33 LUMI-130 — wiki-thoughts (%lookup / %learn): the v0.21 wiki tools in the think path.

Off (master / LUMI_WIKI / LUMI_THOUGHT_WIKI) → absent (plain chat). Mock model + mock HTTP — no network.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 6, 21, 12, 0, tzinfo=UTC))
_SOURCE = "https://uk.wikipedia.org/wiki/Сковорода"


def _fake_http():
    def http_get(url: str) -> str:
        if "opensearch" in url:
            return json.dumps(["Сковорода", ["Сковорода"], ["філософ"], [_SOURCE]])
        if "page/summary" in url:
            return json.dumps({"title": "Сковорода", "extract": "Український філософ.",
                               "content_urls": {"desktop": {"page": _SOURCE}}})
        raise ValueError(f"unexpected url {url}")
    return http_get


def _core(tmp_path, mock, *, master=True, wiki_tool=True, thought_wiki=True):
    return Core(
        llm=mock, repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.", model="m", clock=_CLK, user_id="owner",
        mood_enabled=False, closeness_enabled=False,
        thoughts_enabled=True, thought_tools_enabled=master, thought_wiki=thought_wiki,
        wiki_enabled=wiki_tool, wiki_http_get=_fake_http(),
    )


def test_lookup_runs_the_wiki_loop_and_records_a_thought(tmp_path):
    mock = MockLLMClient("Дізналася дещо нове про нього.\nЕМОЦІЯ: thoughtful",
                         tool_script=[("wiki_search", {"query": "Сковорода"}),
                                      ("wiki_read", {"title": "Сковорода"})])
    core = _core(tmp_path, mock)
    out = core.run_directive("%lookup", core.start_session())
    assert out.is_directive and out.thought.kind == "lookup"
    assert [c[0] for c in mock.tool_calls] == ["wiki_search", "wiki_read"]  # the wiki loop ran


def test_learn_is_also_a_wiki_directive(tmp_path):
    mock = MockLLMClient("Почитала уважно.\nЕМОЦІЯ: calm",
                         tool_script=[("wiki_search", {"query": "філософія"})])
    out = _core(tmp_path, mock).run_directive("%learn", _core(tmp_path, mock).start_session())
    assert out.is_directive and out.thought.kind == "learn"


def test_wiki_thoughts_absent_unless_all_gates_on(tmp_path):
    def mk():
        return MockLLMClient("x\nЕМОЦІЯ: calm", tool_script=[("wiki_search", {"query": "x"})])
    # master off
    assert _core(tmp_path, mk(), master=False).run_directive(
        "%lookup", _core(tmp_path, mk(), master=False).start_session()).is_directive is False
    # the wiki tool off
    assert _core(tmp_path, mk(), wiki_tool=False).run_directive(
        "%lookup", _core(tmp_path, mk(), wiki_tool=False).start_session()).is_directive is False
    # the per-family flag off
    assert _core(tmp_path, mk(), thought_wiki=False).run_directive(
        "%learn", _core(tmp_path, mk(), thought_wiki=False).start_session()).is_directive is False

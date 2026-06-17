"""v0.25 LUMI-102 — the Guardian news tool wired into Core.reply (mock model + mock transport).

A full turn: the model scripts news_search → news_read against an injected mock http_get; Лілі answers
and the {reply, emotion, intensity} contract validates. No network, no key, no paid calls.
"""
from __future__ import annotations

import json
import urllib.parse
from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.emotion import EmotionState
from core.llm import MockLLMClient
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 6, 17, 12, 0, tzinfo=UTC))
_STATE = {"reply": "Читала в Guardian: у світі сьогодні…", "emotion": "thoughtful", "intensity": 0.6}

_SEARCH = {"response": {"status": "ok", "results": [
    {"id": "world/2026/jun/17/a", "webTitle": "A", "webUrl": "https://www.theguardian.com/world/a",
     "sectionName": "World news", "webPublicationDate": "2026-06-17T08:00:00Z",
     "fields": {"headline": "A happens", "trailText": "A summary", "byline": "Reporter A"}},
]}}
_ARTICLE = {"response": {"status": "ok", "content": {
    "id": "world/2026/jun/17/a", "webTitle": "A happens", "webUrl": "https://www.theguardian.com/world/a",
    "fields": {"bodyText": "The full English body of article A.", "byline": "Reporter A"}}}}


def _fake_http():
    seen: list[str] = []

    def http_get(url: str) -> str:
        seen.append(url)
        path = urllib.parse.urlparse(url).path
        return json.dumps(_SEARCH if path.endswith("/search") else _ARTICLE)

    http_get.seen = seen  # type: ignore[attr-defined]
    return http_get


def _core(tmp_path, llm, *, news=False, http=None, user="owner", max_calls=4) -> Core:
    return Core(
        llm=llm, repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.", model="m", clock=_CLK, user_id=user,
        mood_enabled=False, closeness_enabled=False, thoughts_enabled=False,
        tool_max_steps=6, news_enabled=news, news_http_get=http, news_max_calls=max_calls,
        news_api_key="k", news_sections="world,politics,business,technology,science",
    )


def test_turn_searches_then_reads_news_when_on(tmp_path):
    mock = MockLLMClient(states=_STATE, tool_script=[
        ("news_search", {"query": "world today", "topic": "world"}),
        ("news_read", {"id": "n1"}),
    ])
    core = _core(tmp_path, mock, news=True, http=_fake_http())
    state = core.reply("що там у світі сьогодні?", core.start_session())

    assert isinstance(state, EmotionState) and state.emotion.value == "thoughtful"
    assert [c[0] for c in mock.tool_calls] == ["news_search", "news_read"]
    assert "n1: A happens" in mock.tool_calls[0][2]                          # candidates
    assert "The full English body of article A." in mock.tool_calls[1][2]    # read body
    assert "Джерело: https://www.theguardian.com/world/a" in mock.tool_calls[1][2]  # cited


def test_no_news_tools_when_off(tmp_path):
    mock = MockLLMClient(states=_STATE, tool_script=[("news_search", {"topic": "world"})])
    core = _core(tmp_path, mock, news=False, http=_fake_http())
    core.reply("новини?", core.start_session())
    assert mock.tool_calls == []  # off → no news tools offered


def test_per_turn_call_cap(tmp_path):
    mock = MockLLMClient(states=_STATE,
                         tool_script=[("news_search", {"topic": t}) for t in ("world", "business", "science")])
    core = _core(tmp_path, mock, news=True, http=_fake_http(), max_calls=2)
    core.reply("багато новин", core.start_session())
    results = [c[2] for c in mock.tool_calls]
    assert results[0].startswith("Guardian:") and "limit reached" in results[2]  # the 3rd over the cap


def test_news_directive_in_prompt_when_on(tmp_path):
    mock = MockLLMClient(states=_STATE, tool_script=[("news_search", {"topic": "world"}), ("news_read", {"id": "n1"})])
    core = _core(tmp_path, mock, news=True, http=_fake_http())
    core.reply("новини", core.start_session())
    assert "Guardian" in core.last_prompt["system"] and "УКРАЇНСЬКОЮ" in core.last_prompt["system"]

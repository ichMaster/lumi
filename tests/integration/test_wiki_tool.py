"""v0.21 LUMI-089 — the Wikipedia tool wired into Core.reply (mock model + mock HTTP, no network)."""
from __future__ import annotations

import json
from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 6, 16, 12, 0, tzinfo=UTC))
_STATE = {"reply": "Григорій Сковорода — український філософ.", "emotion": "thoughtful", "intensity": 0.6}
_SOURCE = "https://uk.wikipedia.org/wiki/Григорій_Сковорода"


def _fake_http():
    def http_get(url: str) -> str:
        if "opensearch" in url:
            return json.dumps(["Сковорода", ["Григорій Сковорода"], ["український філософ"], [_SOURCE]])
        if "page/summary" in url:
            return json.dumps({"title": "Григорій Сковорода",
                               "extract": "Український філософ, поет і педагог.",
                               "content_urls": {"desktop": {"page": _SOURCE}}})
        raise ValueError(f"unexpected url {url}")
    return http_get


def _core(tmp_path, llm, *, wiki=False, file_tool=False, http=None, user="owner", max_calls=4) -> Core:
    return Core(
        llm=llm, repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.", model="m", clock=_CLK, user_id=user,
        mood_enabled=False, closeness_enabled=False, thoughts_enabled=False,
        file_tool_enabled=file_tool, files_dir=tmp_path / "files", tool_max_steps=6,
        wiki_enabled=wiki, wiki_http_get=http, wiki_max_calls=max_calls,
    )


def test_turn_searches_then_reads_wikipedia_when_on(tmp_path):
    mock = MockLLMClient(states=_STATE, tool_script=[
        ("wiki_search", {"query": "Сковорода"}),
        ("wiki_read", {"title": "Григорій Сковорода"}),
    ])
    core = _core(tmp_path, mock, wiki=True, http=_fake_http())
    state = core.reply("хто такий Сковорода?", core.start_session())

    assert state.emotion.value == "thoughtful"  # {reply, emotion, intensity} valid
    assert [c[0] for c in mock.tool_calls] == ["wiki_search", "wiki_read"]
    assert "Григорій Сковорода" in mock.tool_calls[0][2]                  # search candidates
    assert "Український філософ, поет і педагог." in mock.tool_calls[1][2]  # read extract
    assert f"Джерело: {_SOURCE}" in mock.tool_calls[1][2]                  # answered with the source


def test_no_wiki_tools_when_off(tmp_path):
    mock = MockLLMClient(states=_STATE, tool_script=[("wiki_search", {"query": "x"})])
    core = _core(tmp_path, mock, wiki=False, http=_fake_http())
    core.reply("привіт", core.start_session())
    assert mock.tool_calls == []  # off → executor never invoked, no tools offered


def test_query_passes_through_unchanged_no_personal_data(tmp_path):
    # The handler receives EXACTLY the model's query — the core never augments it with memory.
    mock = MockLLMClient(states=_STATE, tool_script=[("wiki_search", {"query": "гемолімфа"})])
    core = _core(tmp_path, mock, wiki=True, http=_fake_http())
    core.reply("що таке гемолімфа?", core.start_session())
    assert mock.tool_calls[0][1] == {"query": "гемолімфа"}  # input unchanged, no memory appended


def test_wiki_and_file_tools_coexist(tmp_path):
    (tmp_path / "files" / "owner").mkdir(parents=True)
    mock = MockLLMClient(states=_STATE, tool_script=[
        ("wiki_read", {"title": "Григорій Сковорода"}),
        ("list_files", {}),
    ])
    core = _core(tmp_path, mock, wiki=True, file_tool=True, http=_fake_http())
    core.reply("почитай і подивись файли", core.start_session())
    assert [c[0] for c in mock.tool_calls] == ["wiki_read", "list_files"]  # both tool families route
    assert "Джерело:" in mock.tool_calls[0][2]  # the wiki call worked


def test_per_turn_wiki_call_cap(tmp_path):
    mock = MockLLMClient(states=_STATE, tool_script=[("wiki_search", {"query": "a"})] * 3)
    core = _core(tmp_path, mock, wiki=True, http=_fake_http(), max_calls=2)
    core.reply("шукай", core.start_session())
    results = [c[2] for c in mock.tool_calls]
    assert "limit reached" not in results[0] and "limit reached" not in results[1]
    assert "limit reached" in results[2]  # the 3rd wiki call over LUMI_WIKI_MAX_CALLS is refused

"""v0.27 LUMI-108 — the web lookup tool wired into Core.reply (mock model + stub GeminiSearch).

A full turn: the model scripts web_lookup against an injected stub GeminiSearch; Лілі answers and the
{reply, emotion, intensity} contract validates. The prompt is date-anchored from the fixed clock; the web
directive rides the prompt when on. No network, no key, no paid calls.
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.emotion import EmotionState
from core.llm import MockLLMClient
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 6, 18, 12, 0, tzinfo=UTC))
_STATE = {"reply": "Я зараз глянула — у Львові цими вихідними фестиваль.", "emotion": "joy", "intensity": 0.6}


def _stub(answer="Lviv hosts a festival this weekend."):
    seen: list[tuple[str, str]] = []

    def search(query: str, *, today: str) -> str:
        seen.append((query, today))
        return answer

    search.seen = seen  # type: ignore[attr-defined]
    return search


def _core(tmp_path, llm, *, web=False, search=None, user="owner", max_calls=2) -> Core:
    return Core(
        llm=llm, repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.", model="m", clock=_CLK, user_id=user,
        mood_enabled=False, closeness_enabled=False, thoughts_enabled=False,
        tool_max_steps=6, web_lookup_enabled=web, web_search=search, web_lookup_max_calls=max_calls,
    )


def test_turn_does_web_lookup_when_on(tmp_path):
    search = _stub("Lviv hosts a festival this weekend.")
    mock = MockLLMClient(states=_STATE, tool_script=[("web_lookup", {"query": "events in Lviv this weekend"})])
    core = _core(tmp_path, mock, web=True, search=search)
    state = core.reply("що цікавого у Львові цими вихідними?", core.start_session())

    assert isinstance(state, EmotionState) and state.emotion.value == "joy"
    assert [c[0] for c in mock.tool_calls] == ["web_lookup"]
    assert "Lviv hosts a festival this weekend." in mock.tool_calls[0][2]   # the grounded answer
    assert search.seen == [("events in Lviv this weekend", "2026-06-18")]   # query + clock's today


def test_no_web_tool_when_off(tmp_path):
    mock = MockLLMClient(states=_STATE, tool_script=[("web_lookup", {"query": "x"})])
    core = _core(tmp_path, mock, web=False, search=_stub())
    core.reply("що нового?", core.start_session())
    assert mock.tool_calls == []  # off → no web tool offered


def test_per_turn_call_cap(tmp_path):
    mock = MockLLMClient(states=_STATE, tool_script=[("web_lookup", {"query": q}) for q in ("a", "b", "c")])
    core = _core(tmp_path, mock, web=True, search=_stub(), max_calls=2)
    core.reply("багато запитів", core.start_session())
    results = [c[2] for c in mock.tool_calls]
    assert "limit reached" not in results[1] and "limit reached" in results[2]  # the 3rd over the cap


def test_web_directive_in_prompt_when_on(tmp_path):
    mock = MockLLMClient(states=_STATE, tool_script=[("web_lookup", {"query": "x"})])
    core = _core(tmp_path, mock, web=True, search=_stub())
    core.reply("глянь в інтернеті", core.start_session())
    sysprompt = core.last_prompt["system"]
    assert "web_lookup" in sysprompt and "УКРАЇНСЬКОЮ" in sysprompt  # the authored web line rides the prompt

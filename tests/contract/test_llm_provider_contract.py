"""v0.18 contract — every LLM backend honours the LLMClient + emotion-field contract; a turn through
Core.reply yields a valid EmotionState on each provider; provider errors surface as LLMError. All
backends are driven by stubbed transports — **no paid API calls**.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from core.agent import Core
from core.clock import fixed_clock
from core.emotion import EmotionState, validate
from core.llm import (
    AnthropicClient,
    LLMClient,
    LLMError,
    MiniMaxClient,
    MockLLMClient,
    OpenAICompatibleClient,
)
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 6, 14, 12, 0, tzinfo=UTC))


# --- one stubbed client per backend (no network) ---------------------------------------------------
def _anthropic(reply: str = "ок", emotion: str = "joy", intensity: float = 0.9) -> AnthropicClient:
    tool = SimpleNamespace(type="tool_use", name="set_state",
                           input={"reply": reply, "emotion": emotion, "intensity": intensity})
    text = SimpleNamespace(type="text", text=reply)
    usage = SimpleNamespace(input_tokens=10, output_tokens=5,
                            cache_read_input_tokens=0, cache_creation_input_tokens=0)
    resp = SimpleNamespace(content=[text, tool], usage=usage)
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: resp))
    return AnthropicClient("sk-test", _client=client)


def _openai(content: str = '{"reply":"ок","emotion":"joy","intensity":0.9}') -> OpenAICompatibleClient:
    comp = SimpleNamespace(create=lambda **kw: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=None))
    return OpenAICompatibleClient("k", _client=SimpleNamespace(chat=SimpleNamespace(completions=comp)))


def _minimax(content: str = '{"reply":"ок","emotion":"joy","intensity":0.9}') -> MiniMaxClient:
    return MiniMaxClient("k", _transport=lambda u, h, b: {"choices": [{"message": {"content": content}}]})


def _mock() -> MockLLMClient:
    return MockLLMClient("ок", states={"reply": "ок", "emotion": "joy", "intensity": 0.9})


_BACKENDS = [("anthropic", _anthropic), ("openai", _openai), ("minimax", _minimax), ("mock", _mock)]


# --- the LLMClient + emotion-field contract, parametrised over every backend -----------------------
@pytest.mark.parametrize("name,factory", _BACKENDS)
def test_backend_satisfies_llmclient_and_emotion_contract(name, factory):
    client = factory()
    assert isinstance(client, LLMClient)  # reply + reply_structured present
    text = client.reply("sys", [{"role": "user", "content": "hi"}], "m")
    assert isinstance(text, str)
    state = validate(client.reply_structured("sys", [{"role": "user", "content": "hi"}], "m"))
    assert isinstance(state, EmotionState) and state.reply == "ок" and state.emotion.value == "joy"


@pytest.mark.parametrize("name,factory", [
    ("anthropic", lambda: _anthropic(emotion="bogus")),  # unknown emotion → calm
    ("openai", lambda: _openai("not json at all")),
    ("minimax", lambda: _minimax("not json at all")),
])
def test_malformed_output_degrades_to_calm_per_provider(name, factory):
    state = validate(factory().reply_structured("sys", [{"role": "user", "content": "x"}], "m"))
    assert state.emotion.value == "calm"  # never raises


# --- a full turn through Core.reply, per provider --------------------------------------------------
def _core(tmp_path, llm) -> tuple[Core, JsonRepository]:
    repo = JsonRepository(tmp_path / "store.json")
    core = Core(
        llm=llm, repository=repo, canon="Ти — Лілі.", model="m", clock=_CLK,
        mood_enabled=False, closeness_enabled=False, thoughts_enabled=False,
    )
    return core, repo


@pytest.mark.parametrize("name,factory", [("openai", _openai), ("minimax", _minimax), ("mock", _mock)])
def test_core_reply_parity_per_provider(tmp_path, name, factory):
    core, repo = _core(tmp_path, factory())
    session = core.start_session()
    state = core.reply("привіт", session)
    assert isinstance(state, EmotionState) and state.reply == "ок" and state.emotion.value == "joy"
    # both turns persisted (memory written through the same path regardless of provider)
    assert [(m.role, m.text) for m in repo.load_messages(session.id)] == [
        ("user", "привіт"), ("lili", "ок"),
    ]


def test_core_reply_surfaces_provider_error_as_llmerror(tmp_path):
    class _Boom:
        last_thinking = None
        last_stats = None

        def reply(self, **kw):
            raise LLMError("provider down")

        def reply_structured(self, **kw):
            raise LLMError("provider down")

    core, _ = _core(tmp_path, _Boom())
    session = core.start_session()
    with pytest.raises(LLMError):  # surfaced, never a hang
        core.reply("привіт", session)

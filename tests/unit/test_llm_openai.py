"""v0.18 — the OpenAI-compatible backend (OpenAI / DeepSeek / local), stubbed transport, no paid calls."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.config import Config
from core.emotion import validate
from core.llm import (
    LLMClient,
    LLMError,
    OpenAICompatibleClient,
    build_llm,
    parse_emotion_json,
)


# --- a fake `openai` client: records kwargs, returns canned content ---------------------------------
class _FakeCompletions:
    def __init__(self, content: str, usage: object | None = None) -> None:
        self._content = content
        self._usage = usage
        self.last_kwargs: dict | None = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        msg = SimpleNamespace(content=self._content)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=self._usage)


def _fake_openai(content: str, usage: object | None = None) -> SimpleNamespace:
    comp = _FakeCompletions(content, usage)
    return SimpleNamespace(chat=SimpleNamespace(completions=comp), _completions=comp)


def _client(content: str, usage: object | None = None) -> OpenAICompatibleClient:
    return OpenAICompatibleClient("k", _client=_fake_openai(content, usage))


# --- parse helper ----------------------------------------------------------------------------------
def test_parse_emotion_json_plain_fenced_and_embedded():
    assert parse_emotion_json('{"reply":"hi","emotion":"joy","intensity":0.8}')["emotion"] == "joy"
    assert parse_emotion_json('```json\n{"reply":"x","emotion":"calm","intensity":0.2}\n```')["reply"] == "x"
    assert parse_emotion_json('sure: {"reply":"y","emotion":"sad","intensity":0.4} ok')["emotion"] == "sad"


def test_parse_emotion_json_garbage_degrades_to_reply():
    out = parse_emotion_json("not json at all")
    assert out == {"reply": "not json at all"}  # → the v0.3 gate fills emotion=calm


# --- the client ------------------------------------------------------------------------------------
def test_satisfies_llmclient_protocol():
    assert isinstance(_client("{}"), LLMClient)


def test_reply_structured_valid_json_through_v03_gate():
    c = _client('{"reply":"привіт","emotion":"playful","intensity":0.7}')
    payload = c.reply_structured("sys", [{"role": "user", "content": "hi"}], "gpt-x")
    state = validate(payload)
    assert state.reply == "привіт" and state.emotion.value == "playful" and state.intensity == 0.7


def test_reply_structured_malformed_degrades_to_calm_never_raises():
    c = _client("totally not json")
    state = validate(c.reply_structured("sys", [{"role": "user", "content": "x"}], "m"))
    assert state.emotion.value == "calm" and state.reply == "totally not json"


def test_structured_call_requests_json_object_and_appends_instruction():
    c = _client('{"reply":"a","emotion":"calm","intensity":0.5}')
    c.reply_structured("SYSTEM", [{"role": "user", "content": "hi"}], "m")
    kwargs = c._client._completions.last_kwargs
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["messages"][0]["role"] == "system" and "JSON object" in kwargs["messages"][0]["content"]


def test_plain_reply_returns_content_no_json_format():
    c = _client("just text")
    assert c.reply("sys", [{"role": "user", "content": "hi"}], "m") == "just text"
    assert "response_format" not in c._client._completions.last_kwargs


def test_last_stats_populated_from_usage():
    usage = SimpleNamespace(prompt_tokens=120, completion_tokens=40, prompt_tokens_details=None)
    c = _client('{"reply":"a","emotion":"calm","intensity":0.5}', usage)
    c.reply_structured("sys", [{"role": "user", "content": "hi"}], "deepseek-chat")
    assert c.last_stats.input_tokens == 120 and c.last_stats.output_tokens == 40
    assert c.last_stats.model == "deepseek-chat" and c.last_thinking is None


def test_api_error_wrapped_as_llmerror():
    class _Boom:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**_):
                    raise RuntimeError("network down")

    c = OpenAICompatibleClient("k", _client=_Boom(), retries=0)
    with pytest.raises(LLMError, match="OpenAI-compatible call failed"):
        c.reply("sys", [{"role": "user", "content": "hi"}], "m")


# --- v0.37 LUMI-147: reasoning_effort passthrough --------------------------------------------------
def _effort_client(effort: str | None) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        "k", effort=effort, _client=_fake_openai('{"reply":"a","emotion":"calm","intensity":0.5}'))


def _last_kwargs(c: OpenAICompatibleClient) -> dict:
    return c._client._completions.last_kwargs


def test_effort_passed_to_request_when_set():
    c = _effort_client("high")
    c.reply_structured("sys", [{"role": "user", "content": "hi"}], "gpt-5.5")
    assert _last_kwargs(c)["reasoning_effort"] == "high"


def test_effort_omitted_when_unset():
    c = _effort_client(None)
    c.reply_structured("sys", [{"role": "user", "content": "hi"}], "gpt-4o")
    assert "reasoning_effort" not in _last_kwargs(c)


def test_effort_also_on_plain_reply():
    c = _effort_client("medium")
    c.reply("sys", [{"role": "user", "content": "hi"}], "gpt-5.5")
    assert _last_kwargs(c)["reasoning_effort"] == "medium"


def test_effort_clamps_xhigh_and_max_to_high():
    for level in ("xhigh", "max"):
        c = _effort_client(level)
        c.reply_structured("sys", [{"role": "user", "content": "hi"}], "m")
        assert _last_kwargs(c)["reasoning_effort"] == "high"


def test_effort_invalid_value_dropped_safely():
    c = _effort_client("bogus")
    c.reply_structured("sys", [{"role": "user", "content": "hi"}], "m")
    assert "reasoning_effort" not in _last_kwargs(c)  # unknown level → omitted, never sent raw


def test_build_llm_threads_effort_into_openai_client(monkeypatch):
    import pytest
    openai = pytest.importorskip("openai")
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: object())  # no network/SDK auth
    client = build_llm(Config(provider="openai", openai_api_key="k", effort="high"))
    assert isinstance(client, OpenAICompatibleClient) and client._effort == "high"


# --- v0.37 fix: the token param differs by provider (GPT-5 rejects max_tokens) ---------------------
def test_default_token_param_is_max_tokens():
    c = _client('{"reply":"a","emotion":"calm","intensity":0.5}')
    c.reply_structured("sys", [{"role": "user", "content": "hi"}], "deepseek-chat")
    kwargs = c._client._completions.last_kwargs
    assert kwargs["max_tokens"] == 1024 and "max_completion_tokens" not in kwargs


def test_openai_uses_max_completion_tokens_param():
    c = OpenAICompatibleClient(
        "k", max_tokens_param="max_completion_tokens",
        _client=_fake_openai('{"reply":"a","emotion":"calm","intensity":0.5}'))
    c.reply_structured("sys", [{"role": "user", "content": "hi"}], "gpt-5.5")
    kwargs = c._client._completions.last_kwargs
    assert kwargs["max_completion_tokens"] == 1024 and "max_tokens" not in kwargs  # GPT-5 reasoning models


def test_build_llm_picks_token_param_per_provider(monkeypatch):
    openai = pytest.importorskip("openai")
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: object())  # no SDK auth / network
    assert build_llm(Config(provider="openai", openai_api_key="k"))._max_tokens_param == "max_completion_tokens"
    assert build_llm(Config(provider="deepseek", deepseek_api_key="k"))._max_tokens_param == "max_tokens"


# --- factory wiring ---------------------------------------------------------------------------------
def test_factory_builds_openai_compatible_for_each_provider():
    # openai / deepseek with key → an OpenAICompatibleClient (constructed with an injected fake below
    # is not exercised here; we assert the factory dispatches + key checks, not the live SDK).
    with pytest.raises(LLMError, match="OPENAI_API_KEY"):
        build_llm(Config(provider="openai"))
    with pytest.raises(LLMError, match="DEEPSEEK_API_KEY"):
        build_llm(Config(provider="deepseek"))
    with pytest.raises(LLMError, match="LUMI_LLM_BASE_URL"):
        build_llm(Config(provider="local"))

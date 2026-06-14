"""The ``LLMClient`` seam — the only way the core reaches a model.

The core depends on the :class:`LLMClient` Protocol, **never** on a concrete SDK
(ARCHITECTURE §Configuration and secrets). v0.1 has one backend — Anthropic
**Claude Haiku** — plus a :class:`MockLLMClient` for tests (no paid API call).
More models arrive in v0.9 behind this same seam.

v0.1 ``reply(...)`` returns plain text; v0.3 will return a validated
``EmotionState`` (the structured ``{reply, emotion, intensity}`` field).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar, runtime_checkable

from core.emotion import Emotion

# A chat message as the core hands it to the model: an Anthropic-style turn.
Message = dict[str, str]  # {"role": "user" | "assistant", "content": str}

_T = TypeVar("_T")

# The structured-output tool the model fills with its turn: text + her state
# (EMOTION.md §3/§8). `emotion` is enum-constrained, `intensity` to 0–1, so invalid
# values are rare by construction; the v0.3 validation gate is still the safety net.
_EMOTION_TOOL = {
    "name": "set_state",
    "description": "Поверни відповідь Лілі разом із її емоційним станом для цього ходу.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reply": {"type": "string", "description": "Текст відповіді Лілі (лише її слова)."},
            "emotion": {"type": "string", "enum": [e.value for e in Emotion]},
            "intensity": {"type": "number", "minimum": 0, "maximum": 1},
            # v0.10: an ADDITIVE read of the user's message (not Лілі). Optional — the
            # emotion contract (`required` below) is untouched; the core validates it.
            "relation": {
                "type": "object",
                "description": "Оцінка ОСТАННЬОГО повідомлення співрозмовника (не Лілі), кожен вимір 0–1.",
                "properties": {
                    "warmth": {"type": "number", "minimum": 0, "maximum": 1},
                    "vulnerability": {"type": "number", "minimum": 0, "maximum": 1},
                    "playful": {"type": "number", "minimum": 0, "maximum": 1},
                    "harm": {"type": "number", "minimum": 0, "maximum": 1},
                    "manipulation": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        },
        "required": ["reply", "emotion", "intensity"],
    },
}


@dataclass(frozen=True)
class ResponseStats:
    """Per-response stats for the last model call (None fields when unavailable)."""

    model: str
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None    # input tokens served from the prompt cache (v0.15)
    cache_write_tokens: int | None = None   # input tokens written to the cache this turn (v0.15)
    thinking: bool = False


class LLMError(RuntimeError):
    """A model call failed (after any bounded retry).

    Surfaced to the caller as a clear error so the session loop degrades instead
    of hanging (ARCHITECTURE §Error handling).
    """


@runtime_checkable
class LLMClient(Protocol):
    """The seam the core depends on. Backends implement ``reply`` + ``reply_structured``."""

    def reply(
        self, system: str, messages: list[Message], model: str, cache_prefix: str | None = None
    ) -> str:
        """Return the model's plain **text** reply (used for memory housekeeping).

        ``cache_prefix`` (v0.15) — an optional stable prefix of ``system`` to mark as a prompt-cache
        breakpoint; backends without caching ignore it (the assembled text is unchanged).
        """
        ...

    def reply_structured(
        self, system: str, messages: list[Message], model: str, cache_prefix: str | None = None
    ) -> dict:
        """Return the raw structured ``{reply, emotion, intensity}`` (the core validates it).

        ``cache_prefix`` (v0.15) — see :meth:`reply` (prompt-cache breakpoint hint, ignorable).
        """
        ...


def _call_with_retries(
    fn: Callable[[], _T],
    *,
    retries: int,
    backoff: float,
    is_retryable: Callable[[BaseException], bool],
) -> _T:
    """Call ``fn`` with a bounded retry on retryable errors; never hang.

    Retries up to ``retries`` extra times on errors ``is_retryable`` accepts,
    sleeping ``backoff * attempt`` between tries; otherwise re-raises at once.
    Factored out so the retry policy is unit-testable without the SDK.
    """
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — re-raised below
            if is_retryable(exc) and attempt < retries:
                if backoff:
                    time.sleep(backoff * (attempt + 1))
                continue
            raise
    # Unreachable: the loop either returns or raises.
    raise AssertionError("retry loop exited without a result")  # pragma: no cover


class AnthropicClient:
    """Claude Haiku via the official ``anthropic`` SDK.

    The SDK is imported **only here** — the rest of the core depends on the
    :class:`LLMClient` seam. The model id is supplied per call (from config).
    """

    # Anthropic error classes that are worth a bounded retry.
    _RETRYABLE_NAMES = (
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "InternalServerError",
    )

    def __init__(
        self,
        api_key: str | None,
        *,
        max_tokens: int = 1024,
        thinking: bool = False,
        effort: str | None = None,
        cache_ttl: str = "5m",
        retries: int = 2,
        backoff: float = 0.5,
        _client: object | None = None,
    ) -> None:
        if not api_key:
            raise LLMError(
                "ANTHROPIC_API_KEY is not set — cannot reach Claude. "
                "Add it to .env (see .env.example)."
            )
        import anthropic  # SDK imported only inside AnthropicClient

        self._anthropic = anthropic
        self._client = _client if _client is not None else anthropic.Anthropic(api_key=api_key)
        self._max_tokens = max_tokens
        self._thinking = thinking
        self._effort = effort
        # Prompt-cache lifetime: "5m" (default ephemeral) or "1h" (extended — keeps the cached prefix
        # warm across longer gaps, e.g. 10-min proactive thinks; needs the extended-cache-ttl beta).
        self._cache_ttl = cache_ttl
        # The reasoning summary from the most recent turn (None when off/absent),
        # so a client can render it (e.g. greyed) alongside the reply.
        self.last_thinking: str | None = None
        # Per-response stats (latency + token usage) from the last call.
        self.last_stats: ResponseStats | None = None
        self._retries = retries
        self._backoff = backoff
        self._retryable = tuple(
            getattr(anthropic, name) for name in self._RETRYABLE_NAMES if hasattr(anthropic, name)
        )

    def _base_kwargs(
        self, system: str, messages: list[Message], model: str, cache_prefix: str | None = None
    ) -> dict:
        # v0.15 prompt caching: mark the stable prefix as an **ephemeral** cache breakpoint by
        # passing `system` as content blocks — [prefix(cache_control), remainder] — that concatenate
        # back to the original text. Without a (valid) prefix it stays a plain string (byte-identical;
        # an unset/short prefix simply isn't cached, never an error).
        cached = bool(cache_prefix and system.startswith(cache_prefix))
        if cached:
            cache_control = {"type": "ephemeral"}
            if self._cache_ttl == "1h":
                cache_control["ttl"] = "1h"  # extended TTL — survives longer gaps between turns
            blocks = [{"type": "text", "text": cache_prefix, "cache_control": cache_control}]
            remainder = system[len(cache_prefix):]
            if remainder:
                blocks.append({"type": "text", "text": remainder})
            system_field: object = blocks
        else:
            system_field = system
        kwargs: dict = {
            "model": model,
            "system": system_field,
            "max_tokens": self._max_tokens,
            "messages": messages,
        }
        if cached and self._cache_ttl == "1h":  # the 1h cache is behind a beta header
            kwargs["extra_headers"] = {"anthropic-beta": "extended-cache-ttl-2025-04-11"}
        if self._thinking:
            # Adaptive extended thinking (Opus 4.8 / Sonnet 4.6): the model reasons
            # in `thinking` blocks before the visible reply. `display: "summarized"`
            # returns a readable summary (default "omitted") → ``last_thinking``.
            # NB: the legacy {type:"enabled", budget_tokens} form 400s on Opus 4.8.
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
        if self._effort:
            # Tunes thinking depth / token spend (low|medium|high|xhigh|max).
            kwargs["output_config"] = {"effort": self._effort}
        return kwargs

    def _capture(self, resp: object, model: str, latency_ms: int) -> None:
        """Record stats + the reasoning summary from a response (shared by both paths)."""
        usage = getattr(resp, "usage", None)
        self.last_stats = ResponseStats(
            model=model,
            latency_ms=latency_ms,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", None),
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", None),
            thinking=self._thinking,
        )
        self.last_thinking = (
            "".join(
                getattr(block, "thinking", "")
                for block in getattr(resp, "content", [])
                if getattr(block, "type", None) == "thinking"
            ).strip()
            or None
        )

    def reply(
        self, system: str, messages: list[Message], model: str, cache_prefix: str | None = None
    ) -> str:
        def _once() -> str:
            started = time.monotonic()
            resp = self._client.messages.create(
                **self._base_kwargs(system, messages, model, cache_prefix)
            )
            self._capture(resp, model, int((time.monotonic() - started) * 1000))
            return "".join(
                getattr(block, "text", "")
                for block in resp.content
                if getattr(block, "type", None) == "text"
            )

        return self._run(_once, "Claude call failed")

    def reply_structured(
        self, system: str, messages: list[Message], model: str, cache_prefix: str | None = None
    ) -> dict:
        def _once() -> dict:
            kwargs = self._base_kwargs(system, messages, model, cache_prefix)
            kwargs["tools"] = [_EMOTION_TOOL]
            # A forced tool_choice is incompatible with extended thinking, so with
            # thinking on we use "auto" (a strong instruction asks for the tool) and
            # fall back to the text on the rare turn it isn't called.
            kwargs["tool_choice"] = (
                {"type": "auto"} if self._thinking else {"type": "tool", "name": _EMOTION_TOOL["name"]}
            )
            started = time.monotonic()
            resp = self._client.messages.create(**kwargs)
            self._capture(resp, model, int((time.monotonic() - started) * 1000))
            for block in resp.content:
                if (
                    getattr(block, "type", None) == "tool_use"
                    and getattr(block, "name", None) == _EMOTION_TOOL["name"]
                ):
                    return dict(getattr(block, "input", {}) or {})
            # No tool call → degrade to the text; the validation gate fills emotion=calm.
            text = "".join(
                getattr(block, "text", "")
                for block in resp.content
                if getattr(block, "type", None) == "text"
            ).strip()
            return {"reply": text}

        return self._run(_once, "Claude structured call failed")

    def _run(self, fn: Callable[[], _T], err: str) -> _T:
        try:
            return _call_with_retries(
                fn,
                retries=self._retries,
                backoff=self._backoff,
                is_retryable=lambda exc: isinstance(exc, self._retryable),
            )
        except self._anthropic.APIError as exc:  # bounded retry exhausted / non-retryable
            raise LLMError(f"{err}: {exc}") from exc


class MockLLMClient:
    """A canned :class:`LLMClient` for tests — never touches the network.

    ``replies`` may be a single string, a list consumed in order (the last value
    repeats once exhausted), or a callable ``(system, messages, model) -> str``.
    Every call is recorded in :attr:`calls` so tests can assert what the core
    sent. v0.3 will extend this to emit deliberately malformed structured
    replies for the validation gate.
    """

    def __init__(
        self,
        replies: str | list[str] | Callable[[str, list[Message], str], str] | None = None,
        *,
        thinking: str | None = None,
        states: dict | list[dict] | Callable[[str, list[Message], str], dict] | None = None,
    ) -> None:
        self._fn: Callable[[str, list[Message], str], str] | None = None
        self._queue: list[str] | None = None
        self._default = "Привіт. Я Лілі."

        if replies is None:
            pass
        elif isinstance(replies, str):
            self._default = replies
        elif callable(replies):
            self._fn = replies
        else:
            self._queue = list(replies)

        # Optional structured replies for reply_structured (canned or scripted-malformed).
        self._state_fn: Callable[[str, list[Message], str], dict] | None = None
        self._state_queue: list[dict] | None = None
        self._state_default: dict | None = None
        if states is None:
            pass
        elif isinstance(states, dict):
            self._state_default = states
        elif callable(states):
            self._state_fn = states
        else:
            self._state_queue = list(states)

        self.calls: list[dict[str, object]] = []
        self._thinking_text = thinking
        self.last_thinking: str | None = None
        self.last_stats: ResponseStats | None = None

    def _record(self, system: str, messages: list[Message], model: str) -> None:
        self.calls.append({"system": system, "messages": list(messages), "model": model})
        self.last_thinking = self._thinking_text
        self.last_stats = ResponseStats(model=model, latency_ms=0, thinking=False)

    def _pick_text(self, system: str, messages: list[Message], model: str) -> str:
        if self._fn is not None:
            return self._fn(system, messages, model)
        if self._queue:
            value = self._queue.pop(0)
            if not self._queue:
                self._default = value  # last value repeats afterward
            return value
        return self._default

    def reply(
        self, system: str, messages: list[Message], model: str, cache_prefix: str | None = None
    ) -> str:
        self._record(system, messages, model)  # cache_prefix ignored — the text is unchanged
        return self._pick_text(system, messages, model)

    def reply_structured(
        self, system: str, messages: list[Message], model: str, cache_prefix: str | None = None
    ) -> dict:
        self._record(system, messages, model)  # cache_prefix ignored by the mock
        if self._state_fn is not None:
            return self._state_fn(system, messages, model)
        if self._state_queue:
            value = self._state_queue.pop(0)
            if not self._state_queue:
                self._state_default = value
            return dict(value)
        if self._state_default is not None:
            return dict(self._state_default)
        # No canned state → derive a valid one from the text reply.
        return {"reply": self._pick_text(system, messages, model), "emotion": "calm", "intensity": 0.5}

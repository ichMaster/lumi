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
from typing import Protocol, runtime_checkable

# A chat message as the core hands it to the model: an Anthropic-style turn.
Message = dict[str, str]  # {"role": "user" | "assistant", "content": str}


class LLMError(RuntimeError):
    """A model call failed (after any bounded retry).

    Surfaced to the caller as a clear error so the session loop degrades instead
    of hanging (ARCHITECTURE §Error handling).
    """


@runtime_checkable
class LLMClient(Protocol):
    """The seam the core depends on. Backends implement ``reply``."""

    def reply(self, system: str, messages: list[Message], model: str) -> str:
        """Return the model's text reply for ``system`` + ``messages``."""
        ...


def _call_with_retries(
    fn: Callable[[], str],
    *,
    retries: int,
    backoff: float,
    is_retryable: Callable[[BaseException], bool],
) -> str:
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
        self._retries = retries
        self._backoff = backoff
        self._retryable = tuple(
            getattr(anthropic, name) for name in self._RETRYABLE_NAMES if hasattr(anthropic, name)
        )

    def reply(self, system: str, messages: list[Message], model: str) -> str:
        def _once() -> str:
            kwargs: dict = {
                "model": model,
                "system": system,
                "max_tokens": self._max_tokens,
                "messages": messages,
            }
            if self._thinking:
                # Adaptive extended thinking (Opus 4.8 / Sonnet 4.6): the model
                # reasons in `thinking` blocks (which we drop) before the visible
                # `text` reply. NB: the legacy {type:"enabled", budget_tokens}
                # form 400s on Opus 4.8 — adaptive is the only on-mode.
                kwargs["thinking"] = {"type": "adaptive"}
            if self._effort:
                # Tunes thinking depth / token spend (low|medium|high|xhigh|max).
                kwargs["output_config"] = {"effort": self._effort}
            resp = self._client.messages.create(**kwargs)
            return "".join(
                getattr(block, "text", "")
                for block in resp.content
                if getattr(block, "type", None) == "text"
            )

        try:
            return _call_with_retries(
                _once,
                retries=self._retries,
                backoff=self._backoff,
                is_retryable=lambda exc: isinstance(exc, self._retryable),
            )
        except self._anthropic.APIError as exc:  # bounded retry exhausted / non-retryable
            raise LLMError(f"Claude call failed: {exc}") from exc


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

        self.calls: list[dict[str, object]] = []

    def reply(self, system: str, messages: list[Message], model: str) -> str:
        self.calls.append({"system": system, "messages": list(messages), "model": model})
        if self._fn is not None:
            return self._fn(system, messages, model)
        if self._queue:
            value = self._queue.pop(0)
            if not self._queue:
                self._default = value  # last value repeats afterward
            return value
        return self._default

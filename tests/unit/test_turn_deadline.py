"""v2.7 (v2.6 review #4) — a turn behind a hanging model ends at one deadline, not after ~8 minutes.

Live on prod (2026-10-17/19): a streamed turn waited 120 s, fell back to a blocking call with 120 s and two retries —
up to ~8 min while the client's next line waited too. Now every model call of a turn shares one budget
(``TURN_DEADLINE_S``): each request's timeout is what is left, no retry or fallback starts once it is spent, and the
turn ends with a readable ``TurnDeadlineExceeded``. A fake clock drives the "hang" — nothing really waits; no network.
"""

import threading

import pytest
from fastapi.testclient import TestClient

import core.llm as llm
from core.agent import Core
from core.llm import (
    HTTP_TIMEOUT_S,
    TURN_DEADLINE_S,
    GeminiClient,
    LLMError,
    MockLLMClient,
    TurnDeadlineExceeded,
    _call_with_retries,
)
from server.app import create_app
from state.local_store import JsonRepository

_VALID = '{"reply":"привіт","emotion":"playful","intensity":0.7}'
_MSG = [{"role": "user", "content": "привіт"}]
_MODEL = "gemini-3.1-pro-preview"


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _hanging(clock: _Clock, gem_ref: list) -> tuple[list, list]:
    """A model that never answers: each request waits out its whole timeout, then times out."""
    calls: list[str] = []

    def wait() -> None:
        left = gem_ref[0]._remaining()
        clock.t += HTTP_TIMEOUT_S if left is None else min(HTTP_TIMEOUT_S, left)

    def post(url, headers, body):
        calls.append("blocking")
        wait()
        raise TimeoutError("The read operation timed out")

    def stream(url, headers, body):
        def frames():
            calls.append("stream")
            wait()
            raise TimeoutError("The read operation timed out")
            yield  # pragma: no cover — a generator that only fails

        return frames()

    return [post, stream], calls


def _gem(clock: _Clock, **kw) -> tuple[GeminiClient, list]:
    ref: list = []
    (post, stream), calls = _hanging(clock, ref)
    gem = GeminiClient("k", _transport=post, _stream_transport=stream, _clock=clock, backoff=0.0, **kw)
    ref.append(gem)
    return gem, calls


def test_a_hanging_streamed_turn_ends_at_the_deadline_not_after_eight_minutes():
    clock = _Clock()
    gem, calls = _gem(clock)
    start = clock.t
    with pytest.raises(TurnDeadlineExceeded, match="/model"):
        gem.reply_structured_stream("sys", _MSG, _MODEL, on_delta=lambda _: None)
    assert clock.t - start == pytest.approx(TURN_DEADLINE_S)  # was 120 + 3 × 120 = 480 s before v2.7
    assert calls == ["stream", "blocking"]  # the fallback got only what was left; no retry after it


def test_the_blocking_retries_stop_at_the_deadline():
    clock = _Clock()
    gem, calls = _gem(clock)
    start = clock.t
    with pytest.raises(TurnDeadlineExceeded):
        gem.reply_structured("sys", _MSG, _MODEL)
    assert clock.t - start == pytest.approx(TURN_DEADLINE_S)
    assert calls == ["blocking", "blocking"]  # 120 s, then the 60 s left — not a third attempt


def test_a_fast_model_is_unaffected_and_the_budget_ends_with_the_turn():
    clock = _Clock()
    gem = GeminiClient("k", _transport=lambda u, h, b: {"candidates": [{"content": {"parts": [{"text": _VALID}]}}]},
                       _clock=clock)
    assert gem.reply_structured("sys", _MSG, _MODEL)["reply"] == "привіт"
    assert gem._remaining() is None and gem._timeout() == HTTP_TIMEOUT_S  # outside a turn: the plain ceiling


def test_a_thought_on_another_thread_keeps_its_own_budget():
    clock = _Clock()
    gem = GeminiClient("k", _transport=lambda u, h, b: {}, _clock=clock)
    seen: list = []
    with gem._turn():
        clock.t += TURN_DEADLINE_S + 1  # this thread's turn is spent…
        with pytest.raises(TurnDeadlineExceeded):
            gem._timeout()
        other = threading.Thread(target=lambda: seen.append(gem._timeout()))  # …a thought elsewhere isn't
        other.start()
        other.join()
    assert seen == [HTTP_TIMEOUT_S]


def test_the_retry_helper_never_retries_past_a_deadline(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(llm.time, "sleep", lambda s: setattr(clock, "t", clock.t + s))
    calls: list[int] = []

    def fails():
        calls.append(1)
        raise TimeoutError("timed out")

    with pytest.raises(TimeoutError):
        _call_with_retries(fails, retries=5, backoff=0.5, is_retryable=lambda e: True,
                           deadline=clock.t + 1.0, clock=clock)
    assert len(calls) == 2  # tried, slept 0.5, tried, then 1.0 more would cross the deadline — stopped
    with pytest.raises(TimeoutError):  # no deadline → the old bounded retries, unchanged
        _call_with_retries(fails, retries=2, backoff=0.0, is_retryable=lambda e: True)
    assert len(calls) == 5


def test_the_server_says_so_instead_of_hanging(tmp_path):
    def timed_out(system, messages, model):
        raise TurnDeadlineExceeded()

    core = Core(llm=MockLLMClient(states=timed_out), repository=JsonRepository(tmp_path / "s.json"),
                canon="Ти — Лілі.", model="m")
    client = TestClient(create_app(core, token="t"))
    res = client.post("/v1/turn", json={"text": "привіт"}, headers={"Authorization": "Bearer t"})
    assert res.status_code == 502 and "didn't answer within 3 minutes" in res.json()["detail"]
    assert issubclass(TurnDeadlineExceeded, LLMError)  # every surface that handles a model failure handles this

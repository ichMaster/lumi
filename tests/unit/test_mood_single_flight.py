"""Today's mood is computed once, even when two callers race (code review v2.4 #1).

Live, 2026-10-11: the server's startup mood was still in flight when the first message arrived; the turn's
own mood check saw nothing yet and called the model again — two different readings three seconds apart. A
second caller now waits for the first and reuses its reading. Mock model; the interleaving is driven.
"""

import threading
import time
from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from state.local_store import JsonRepository

_NATAL = "Народження: 05.03.2002, 10:15, Львів.\nСонце 15° Риб."
_DAY = fixed_clock(datetime(2026, 10, 11, 1, 35, tzinfo=UTC))


def _core(tmp_path, llm):
    return Core(llm=llm, repository=JsonRepository(tmp_path / "s.json"), canon="Ти — Лілі.", model="m",
                clock=_DAY, natal=_NATAL, mood_enabled=True, biorhythms_enabled=False)


def test_a_racing_second_caller_waits_and_reuses_the_reading(tmp_path):
    calls: list[int] = []
    in_flight = threading.Event()

    def slow_mood(system, messages, model):
        calls.append(1)
        in_flight.set()
        time.sleep(0.3)  # the ~30 s mood call, compressed
        return f"Читання №{len(calls)}.\n\nРЕЗОЛЮЦІЯ:\nНастрій №{len(calls)}."

    core = _core(tmp_path, MockLLMClient(slow_mood))
    startup = threading.Thread(target=core.ensure_mood)  # the server's background mood at start
    startup.start()
    assert in_flight.wait(2)
    core.ensure_mood()  # the first turn's own check, while the startup call is still running
    startup.join(2)
    assert calls == [1]  # one model call, not two
    assert core.mood == "Настрій №1."  # both callers end on the same reading


def test_a_failed_mood_call_still_lets_the_next_caller_try(tmp_path):
    answers = iter([RuntimeError("model down"), "Інший день.\n\nРЕЗОЛЮЦІЯ:\nСпокійно."])

    def flaky(system, messages, model):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    core = _core(tmp_path, MockLLMClient(flaky))
    core.ensure_mood()  # best-effort: no mood, no raise — and the lock is not kept
    assert core.mood is None
    core.ensure_mood()
    assert core.mood == "Спокійно."

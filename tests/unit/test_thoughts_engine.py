"""The mental-act engine + registry + %think / %wonder (v0.12, LUMI-047) — mock model."""

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from core.thoughts import REGISTRY, THINK, WONDER, parse_thought, thought_request
from state.local_store import JsonRepository

_DAY = fixed_clock(datetime(2026, 6, 9, 14, 30, tzinfo=UTC))


def _core(tmp_path, llm, *, enabled=True):
    return Core(
        llm=llm, repository=JsonRepository(tmp_path / "s.json"),
        canon="C", model="m", clock=_DAY, mood_enabled=False, thoughts_enabled=enabled,
    )


# --- registry + pure helpers ----------------------------------------------
def test_registry_has_think_and_wonder():
    assert set(REGISTRY) == {"think", "wonder"}
    assert REGISTRY["think"] is THINK and REGISTRY["wonder"] is WONDER


def test_thought_request_seeds_state_and_seed():
    system, msgs = thought_request(THINK, mood="жвавий день", topic="той трек", rng_seed=3)
    content = msgs[0]["content"]
    assert "жвавий день" in content and "той трек" in content and "№3" in content
    assert "ЕМОЦІЯ" in system  # asks for the emotion tag


def test_parse_thought_strips_tag_and_defaults():
    assert parse_thought("щось крутиться\nЕМОЦІЯ: tender") == ("щось крутиться", "tender")
    assert parse_thought("гола думка без тегу") == ("гола думка без тегу", "calm")
    assert parse_thought("   ") is None
    assert parse_thought("ЕМОЦІЯ: joy") is None  # only the tag → nothing


# --- the engine records into the diary ------------------------------------
def test_think_records_a_structured_thought(tmp_path):
    core = _core(tmp_path, MockLLMClient("ще зранку кручу той бридж\nЕМОЦІЯ: thoughtful"))
    t = core.think("think", rng_seed=1)
    assert t is not None
    assert t.kind == "think" and t.text == "ще зранку кручу той бридж" and t.emotion == "thoughtful"
    assert t.when == "2026-06-09T14:30"
    # it's in the global diary
    assert core._repo.thoughts_since("2026-06-09T00:00")[0].text == "ще зранку кручу той бридж"


def test_wonder_records_kind_wonder(tmp_path):
    core = _core(tmp_path, MockLLMClient("а що, якби небо було ще тихішим\nЕМОЦІЯ: surprise"))
    t = core.think("wonder")
    assert t is not None and t.kind == "wonder" and t.emotion == "surprise"


def test_unknown_emotion_clamps_to_calm(tmp_path):
    core = _core(tmp_path, MockLLMClient("дивна думка\nЕМОЦІЯ: euphoria"))
    t = core.think("think")
    assert t is not None and t.emotion == "calm"  # not in the base-9 set → calm


def test_malformed_records_nothing(tmp_path):
    core = _core(tmp_path, MockLLMClient("   "))
    assert core.think("think") is None
    assert core._repo.thoughts_since("2026-01-01") == []  # nothing recorded


def test_unknown_directive_and_disabled(tmp_path):
    assert _core(tmp_path, MockLLMClient("x\nЕМОЦІЯ: joy")).think("dream") is None  # not registered
    assert _core(tmp_path, MockLLMClient("x\nЕМОЦІЯ: joy"), enabled=False).think("think") is None


def test_seeds_recorded_on_the_thought(tmp_path):
    core = _core(tmp_path, MockLLMClient("думка про це\nЕМОЦІЯ: calm"))
    t = core.think("think", topic="море")
    assert "topic" in t.seeds  # the topic seed is recorded


def test_think_block_is_stripped(tmp_path):
    # full-context mode reuses the reply backdrop (which asks for <think>…</think>) — strip it.
    reply = "<think>міркую сама із собою</think>\n\nа може, крейда миліша за коло\nЕМОЦІЯ: thoughtful"
    t = _core(tmp_path, MockLLMClient(reply)).think("think")
    assert t is not None and t.text == "а може, крейда миліша за коло"
    assert "<think>" not in t.text and "міркую" not in t.text

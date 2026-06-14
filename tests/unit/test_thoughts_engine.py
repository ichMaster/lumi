"""The mental-act engine + registry + %think / %wonder (v0.12, LUMI-047) — mock model."""

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient, ResponseStats
from core.repository import make_message
from core.thoughts import REGISTRY, THINK, WONDER, parse_thought, thought_request
from state.local_store import JsonRepository

_DAY = fixed_clock(datetime(2026, 6, 9, 14, 30, tzinfo=UTC))


class _CacheRec:
    """A fake LLM that records the cache_prefix each call received (to test think-time caching)."""

    def __init__(self, text):
        self._text = text
        self.calls: list[dict] = []
        self._thinking = False
        self.last_thinking = None
        self.last_stats = None

    def _record(self, system, model, cache_prefix):
        self.calls.append({"system": system, "cache_prefix": cache_prefix})
        self.last_stats = ResponseStats(model=model, latency_ms=0)

    def reply(self, system, messages, model, cache_prefix=None):
        self._record(system, model, cache_prefix)
        return self._text

    def reply_structured(self, system, messages, model, cache_prefix=None):
        self._record(system, model, cache_prefix)
        return {"reply": self._text, "emotion": "calm", "intensity": 0.5}


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


# --- v0.17.x: the full-mode think reuses the reply cache prefix (so frequent thinks keep it warm) ---
def test_full_mode_think_marks_the_cache_prefix(tmp_path):
    rec = _CacheRec("думка\nЕМОЦІЯ: calm")
    core = Core(llm=rec, repository=JsonRepository(tmp_path / "s.json"), canon="CANON",
                model="m", clock=_DAY, mood_enabled=False, thoughts_enabled=True,
                thoughts_context="full", prompt_cache=True)
    s = core.start_session()
    core._repo.append_message(
        make_message(s.id, "owner", "user", "привіт", ts="2026-06-09T14:00:00+00:00")
    )
    core.think("think", session=s, rng_seed=1)
    call = rec.calls[-1]
    assert call["cache_prefix"]                              # the full think marks a cache prefix…
    assert call["system"].startswith(call["cache_prefix"])  # …and the startswith invariant holds


def test_lean_mode_think_has_no_cache_prefix(tmp_path):
    rec = _CacheRec("думка\nЕМОЦІЯ: calm")
    core = Core(llm=rec, repository=JsonRepository(tmp_path / "s.json"), canon="C",
                model="m", clock=_DAY, mood_enabled=False, thoughts_enabled=True,
                thoughts_context="lean", prompt_cache=True)
    s = core.start_session()
    core.think("think", session=s, rng_seed=1)
    assert rec.calls[-1]["cache_prefix"] is None             # lean → small prompt, nothing cached


def test_full_mode_think_skips_cache_prefix_when_caching_off(tmp_path):
    rec = _CacheRec("думка\nЕМОЦІЯ: calm")
    core = Core(llm=rec, repository=JsonRepository(tmp_path / "s.json"), canon="CANON",
                model="m", clock=_DAY, mood_enabled=False, thoughts_enabled=True,
                thoughts_context="full", prompt_cache=False)
    s = core.start_session()
    core._repo.append_message(
        make_message(s.id, "owner", "user", "привіт", ts="2026-06-09T14:00:00+00:00")
    )
    core.think("think", session=s, rng_seed=1)
    assert rec.calls[-1]["cache_prefix"] is None             # caching off → no breakpoint passed

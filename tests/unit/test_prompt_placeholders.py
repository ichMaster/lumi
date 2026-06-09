"""The {name} prompt-placeholder resolver (v0.12, LUMI-048) — registry, unknown→literal, isolation."""

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from core.placeholders import PLACEHOLDER_NAMES, resolve_placeholders
from core.repository import make_thought
from state.local_store import JsonRepository

_DAY = fixed_clock(datetime(2026, 6, 9, 14, 30, tzinfo=UTC))


# --- the pure resolver ----------------------------------------------------
def test_resolve_substitutes_known_and_leaves_unknown_literal():
    out = resolve_placeholders(
        "mood={mood}, who={user}, ghost={ghost}",
        {"mood": lambda: "жвавий", "user": lambda: "owner"},
    )
    assert out == "mood=жвавий, who=owner, ghost={ghost}"  # unknown stays literal


def test_empty_value_substitutes_empty_failing_getter_literal():
    def boom() -> str:
        raise RuntimeError("nope")

    out = resolve_placeholders("[{plan}][{bad}]", {"plan": lambda: "", "bad": boom})
    assert out == "[][{bad}]"  # empty → "", a raising getter → literal


def test_registry_documents_the_tokens():
    assert {"last_thought", "thoughts", "mood", "now", "user"} <= PLACEHOLDER_NAMES


# --- the Core wiring ------------------------------------------------------
def _core(tmp_path, user="owner"):
    return Core(llm=MockLLMClient("x"), repository=JsonRepository(tmp_path / "s.json"),
                canon="C", model="m", clock=_DAY, mood_enabled=False, user_id=user)


def test_core_resolves_state_tokens(tmp_path):
    core = _core(tmp_path)
    assert core.resolve("{now}") == "2026-06-09 14:30"
    assert core.resolve("{today}") == "2026-06-09"
    assert core.resolve("{user}") == "owner"
    assert core.resolve("hi {nope}") == "hi {nope}"  # unknown literal


def test_last_thought_resolves_from_store(tmp_path):
    core = _core(tmp_path)
    core._repo.add_thought(
        make_thought("2026-06-09T10:00", "think", "той трек", "calm", ["mood"], "owner"))
    assert core.resolve("ще думаю про: {last_thought}") == "ще думаю про: той трек"


def test_last_thought_is_isolation_aware(tmp_path):
    p = tmp_path / "s.json"
    JsonRepository(p).add_thought(
        make_thought("2026-06-09T10:00", "think", "alice secret", "calm", ["mood"], "alice"))
    bob = Core(llm=MockLLMClient("x"), repository=JsonRepository(p), canon="C", model="m",
               clock=_DAY, mood_enabled=False, user_id="bob")
    assert bob.resolve("{last_thought}") == ""  # bob never sees alice's thought

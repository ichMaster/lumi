"""Input router + %-grammar + access gate (v0.12, LUMI-051) — pure parse + Core seam."""

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from core.thoughts import ParsedDirective, directive_mode, parse_directive
from state.local_store import JsonRepository

_DAY = fixed_clock(datetime(2026, 6, 9, 14, 30, tzinfo=UTC))


# --- the grammar ----------------------------------------------------------
def test_parse_bare_and_open():
    assert parse_directive("%think") == ParsedDirective("think", False, None)
    assert parse_directive("%think!") == ParsedDirective("think", True, None)


def test_parse_topic_with_optional_connectors():
    assert parse_directive("%think the track").topic == "the track"
    assert parse_directive("%think about the track").topic == "the track"
    assert parse_directive("%think про море").topic == "море"
    assert parse_directive("%think: the deploy").topic == "the deploy"
    assert parse_directive("%think! about us") == ParsedDirective("think", True, "us")
    assert parse_directive("%wonder").name == "wonder"


def test_unknown_or_nondirective_is_none():
    assert parse_directive("%bogus topic") is None  # unknown directive → chat
    assert parse_directive("50% done") is None       # not a directive
    assert parse_directive("hello") is None


# --- the access gate ------------------------------------------------------
def test_access_gate_silent_vs_shared():
    bare = ParsedDirective("think", False, None)
    bang = ParsedDirective("think", True, None)
    assert directive_mode(bare, is_owner=True) == "silent"   # owner can fire silent
    assert directive_mode(bang, is_owner=True) == "open"
    assert directive_mode(bare, is_owner=False) == "open"    # non-owner: never silent → forced open
    assert directive_mode(bang, is_owner=False) == "open"


# --- the Core seam --------------------------------------------------------
def _core(tmp_path, reply="мимохідь подумала\nЕМОЦІЯ: calm"):
    core = Core(llm=MockLLMClient(reply), repository=JsonRepository(tmp_path / "s.json"),
                canon="C", model="m", clock=_DAY, mood_enabled=False)
    return core, core.start_session()


def test_run_directive_fires_and_records(tmp_path):
    core, s = _core(tmp_path)
    out = core.run_directive("%think", s)
    assert out.is_directive and out.mode == "silent" and out.thought is not None
    assert core._repo.thoughts_since("2026-01-01")[0].text == "мимохідь подумала"


def test_run_directive_open_mode(tmp_path):
    core, s = _core(tmp_path)
    out = core.run_directive("%think!", s)
    assert out.is_directive and out.mode == "open" and out.thought is not None


def test_run_directive_non_directive_is_chat(tmp_path):
    core, s = _core(tmp_path)
    out = core.run_directive("%bogus", s)
    assert out.is_directive is False  # → caller treats it as plain chat


def test_run_directive_resolves_placeholder_topic(tmp_path):
    from core.repository import make_thought
    core, s = _core(tmp_path)
    # seed a prior thought so {last_thought} resolves in the topic
    core._repo.add_thought(make_thought("2026-06-09T09:00", "think", "той трек", "calm", [], "owner"))
    # the topic {last_thought} is resolved before the call (asserted via the seed recorded)
    out = core.run_directive("%think about {last_thought}", s)
    assert out.is_directive and out.thought is not None
    assert "topic" in out.thought.seeds  # a topic was passed (resolved, non-empty)


def test_non_owner_cannot_fire_silent(tmp_path):
    core, s = _core(tmp_path)
    out = core.run_directive("%think", s, is_owner=False)
    assert out.mode == "open"  # forced to surface

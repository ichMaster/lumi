"""Contract — v0.15 prompt caching: the cache prefix is **stable** and **isolated**, and the
breakpoint **toggles**.

Pins the two invariants that make caching correct: (1) the cacheable prefix never drifts across
turns of a session — so Anthropic's server cache keeps hitting — and (2) no per-turn block (the
ambient timestamp, the closeness block, the thoughts) ever leaks into it. Plus: the toggle controls
whether the hint is passed, and the mock backend ignores it (identical reply). No network / paid calls.
"""

from datetime import UTC, datetime, timedelta

from core.agent import Core
from core.llm import MockLLMClient
from core.prompt import build_system_prompt
from core.repository import Closeness, make_thought
from core.worldcontext import WorldContext
from state.local_store import JsonRepository

NOW = datetime(2026, 6, 12, 2, 0, tzinfo=UTC)


class _Clock:
    """A mutable injected clock — advance ``dt`` to change the ambient timestamp."""

    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt


class _CacheRecorder(MockLLMClient):
    """A mock that records the ``cache_prefix`` the core passed (otherwise the mock ignores it)."""

    last_cache_prefix: object = "unset"

    def reply_structured(self, system, messages, model, cache_prefix=None, **_):
        self.last_cache_prefix = cache_prefix
        return super().reply_structured(system, messages, model)


def _core(tmp_path, clock, *, prompt_cache=True, llm=None):
    core = Core(
        llm=llm or MockLLMClient(states={"reply": "ок", "emotion": "calm", "intensity": 0.5}),
        repository=JsonRepository(tmp_path / "s.json"), canon="CANON", model="m", clock=clock,
        mood_enabled=False, biorhythms_enabled=False, cycle_enabled=False,
        closeness_enabled=True, thoughts_enabled=True, prompt_cache=prompt_cache,
        closeness_levels={3: ("Своя", "поведінка-3"), 5: ("Найрідніша", "поведінка-5")},
    )
    core.set_world_context(WorldContext(location="Львів"))  # ambient block present (timestamp per turn)
    return core


def _churn_the_tail(core, clock):
    """Change every per-turn input: advance the clock + new closeness level + a new thought."""
    clock.dt = NOW + timedelta(minutes=17)
    core._repo.set_closeness(Closeness("owner", 95.0, 5, clock.dt.isoformat()))
    core._repo.add_thought(make_thought("2026-06-12T01:50", "think", "НОВА-ДУМКА", "calm", (), "owner"))


def test_cache_prefix_byte_identical_across_turns_while_tail_changes(tmp_path):
    clock = _Clock(NOW)
    core = _core(tmp_path, clock)
    session = core.start_session()
    core._repo.set_closeness(Closeness("owner", 50.0, 3, NOW.isoformat()))  # level 3
    _, prefix1 = core._system_prompt(session)

    _churn_the_tail(core, clock)
    sys2, prefix2 = core._system_prompt(session)

    assert prefix2 == prefix1        # the cache_prefix never drifts (the server cache keeps hitting)
    assert sys2.startswith(prefix2)  # still a true prefix of the full system


def test_in_session_digest_rides_the_tail_not_the_cache_prefix():
    # The in-session digest grows on compaction (every LUMI_COMPACTION_BATCH messages); keeping it OFF
    # the cached prefix means a compaction never re-writes the static head (the cache-write fix).
    sys_a, prefix_a = build_system_prompt("CANON", facts=["f1"], digest="DIGEST-V1")
    sys_b, prefix_b = build_system_prompt("CANON", facts=["f1"], digest="DIGEST-V2")
    assert "Раніше в цій розмові" in sys_a and "DIGEST-V1" in sys_a  # present, in the tail
    assert "DIGEST-V1" not in prefix_a                               # never in the cached prefix
    assert prefix_a == prefix_b                                      # a digest change can't drift the prefix
    assert sys_a.startswith(prefix_a) and "f1" in prefix_a           # facts stay cached; still a true prefix


def test_per_turn_blocks_never_enter_the_cache_prefix(tmp_path):
    clock = _Clock(NOW)
    core = _core(tmp_path, clock)
    _churn_the_tail(core, clock)
    sys_text, prefix = core._system_prompt(core.start_session())
    for per_turn in ("Найрідніша", "НОВА-ДУМКА", "час:", "Львів"):
        assert per_turn in sys_text, f"{per_turn} missing from the assembled system"
        assert per_turn not in prefix, f"{per_turn} leaked into the cache prefix"


def test_toggle_controls_whether_the_cache_prefix_is_passed(tmp_path):
    on = _CacheRecorder(states={"reply": "ок", "emotion": "calm", "intensity": 0.5})
    core_on = _core(tmp_path / "on", _Clock(NOW), prompt_cache=True, llm=on)
    core_on.reply("привіт", core_on.start_session())
    assert on.last_cache_prefix is not None  # ON → the cache_prefix is passed to the model

    off = _CacheRecorder(states={"reply": "ок", "emotion": "calm", "intensity": 0.5})
    core_off = _core(tmp_path / "off", _Clock(NOW), prompt_cache=False, llm=off)
    core_off.reply("привіт", core_off.start_session())
    assert off.last_cache_prefix is None  # OFF → not passed (plain-string system at the adapter)


def test_mock_backend_ignores_the_cache_prefix_and_replies_identically(tmp_path):
    # the same reply with caching on vs off — the mock ignores the hint entirely.
    a = _core(tmp_path / "a", _Clock(NOW), prompt_cache=True)
    b = _core(tmp_path / "b", _Clock(NOW), prompt_cache=False)
    sa = a.reply("привіт", a.start_session())
    sb = b.reply("привіт", b.start_session())
    assert sa.reply == sb.reply == "ок"

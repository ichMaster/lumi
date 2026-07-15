"""v1.4 LUMI-188 — the streaming reply path through Core.reply (on_delta), off-pin, think routing."""
from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 7, 15, 12, 0, tzinfo=UTC))


def _core(tmp_path, llm, *, stream: bool):
    return Core(
        llm=llm,
        repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.",
        model="mock",
        clock=_CLK,
        stream=stream,
    )


def test_streamed_reply_assembles_to_the_state_reply(tmp_path):
    llm = MockLLMClient(states={"reply": "Привіт, як ти сьогодні?", "emotion": "joy", "intensity": 0.7},
                        stream_chunk=4)
    core = _core(tmp_path, llm, stream=True)
    deltas: list[str] = []
    state = core.reply("привіт", core.start_session(), on_delta=deltas.append)
    assert state.reply == "Привіт, як ти сьогодні?"
    assert "".join(deltas) == state.reply          # the shown stream reassembles to the final reply
    assert len(deltas) > 1                          # genuinely incremental


def test_stream_off_never_calls_on_delta(tmp_path):
    # Off-pin: LUMI_STREAM off → the blocking path; on_delta is never invoked, the turn still works.
    llm = MockLLMClient(states={"reply": "добре", "emotion": "calm", "intensity": 0.5})
    core = _core(tmp_path, llm, stream=False)
    deltas: list[str] = []
    state = core.reply("привіт", core.start_session(), on_delta=deltas.append)
    assert state.reply == "добре"
    assert deltas == []                             # streaming off → no deltas, blocking path taken


def test_stream_routes_think_and_shows_only_prose(tmp_path):
    # <think> in the reply field routes to on_think_delta; the shown stream is just the prose.
    llm = MockLLMClient(states={"reply": "<think>зважую слова</think>Готово, друже", "emotion": "calm",
                                "intensity": 0.5})
    core = _core(tmp_path, llm, stream=True)
    shown: list[str] = []
    think: list[str] = []
    state = core.reply("привіт", core.start_session(), on_delta=shown.append, on_think_delta=think.append)
    assert state.reply == "Готово, друже"
    assert "".join(shown) == "Готово, друже"        # the <think> block never reaches the shown stream
    assert "зважую слова" in "".join(think)
    assert "<" not in "".join(shown)


def test_streamed_and_blocking_produce_the_same_state(tmp_path):
    # The streamed turn's EmotionState equals the blocking turn's (contract resolved at completion).
    canned = {"reply": "однакова відповідь", "emotion": "tender", "intensity": 0.8}
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a = _core(tmp_path / "a", MockLLMClient(states=dict(canned)), stream=True)
    b = _core(tmp_path / "b", MockLLMClient(states=dict(canned)), stream=False)
    sa = a.reply("q", a.start_session(), on_delta=lambda _c: None)
    sb = b.reply("q", b.start_session())
    assert (sa.reply, sa.emotion, sa.intensity) == (sb.reply, sb.emotion, sb.intensity)

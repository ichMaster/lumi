"""Integration test for the core turn (LUMI-005).

A full turn `user_text → reply` against MockLLMClient (no paid call), asserting
the model is reached only via the LLMClient seam and both messages persist.
"""

from datetime import UTC, datetime

from core.agent import Core, build_core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from state.local_store import JsonRepository

# A fixed clock makes the per-message timestamps deterministic (v0.4).
_CLK = fixed_clock(datetime(2026, 6, 7, 14, 30, tzinfo=UTC))


def _core_with(tmp_path, llm):
    repo = JsonRepository(tmp_path / "store.json")
    return Core(
        llm=llm,
        repository=repo,
        canon="Ти — Лілі.",
        model="claude-haiku-4-5-20251001",
        clock=_CLK,
    ), repo


def test_full_turn_returns_reply_and_persists_both_messages(tmp_path):
    llm = MockLLMClient("Привіт. Я Лілі.")
    core, repo = _core_with(tmp_path, llm)

    session = core.start_session()
    out = core.reply("Привіт!", session)

    assert out.reply == "Привіт. Я Лілі."  # v0.3: reply() returns an EmotionState
    msgs = repo.load_messages(session.id)
    assert [(m.role, m.text) for m in msgs] == [
        ("user", "Привіт!"),
        ("lili", "Привіт. Я Лілі."),
    ]


def test_turn_sends_canon_and_history_to_the_model(tmp_path):
    llm = MockLLMClient(["перша", "друга"])
    core, _ = _core_with(tmp_path, llm)
    session = core.start_session()

    core.reply("раз", session)
    core.reply("два", session)

    # Second call must carry the canon as system + prior turns + the new line.
    # Лілі's prior reply is replayed with its <emotion> tag reconstructed (the mock
    # derives a calm/0.5 state) so the model keeps emitting the tag.
    second = llm.calls[1]
    assert second["system"].startswith("Ти — Лілі.")
    assert second["model"] == "claude-haiku-4-5-20251001"
    # Each message is prefixed with its date-time (v0.4); the assistant turn also
    # carries the reconstructed <emotion> tag.
    assert second["messages"] == [
        {"role": "user", "content": "[2026-06-07 14:30] раз"},
        {"role": "assistant", "content": "[2026-06-07 14:30] перша <emotion>calm 0.5</emotion>"},
        {"role": "user", "content": "[2026-06-07 14:30] два"},
    ]


def test_history_persists_across_a_restart(tmp_path):
    path = tmp_path / "store.json"
    llm = MockLLMClient("ага")
    core = Core(
        llm=llm,
        repository=JsonRepository(path),
        canon="Ти — Лілі.",
        model="m",
    )
    session = core.start_session()
    core.reply("привіт", session)

    # New core + store over the same file: history is still there.
    reopened = JsonRepository(path)
    msgs = reopened.load_messages(session.id)
    assert len(msgs) == 2


def test_build_core_wires_from_config_with_injected_llm_and_repo(tmp_path):
    # build_core never touches the Anthropic SDK when an llm is injected.
    from dataclasses import replace

    from core.config import load_config

    llm = MockLLMClient("ok")
    repo = JsonRepository(tmp_path / "store.json")
    # Isolate the store dir so the daily mood log lands in tmp, not the real .lumi/.
    cfg = replace(load_config(), store_path=tmp_path / "store.json")
    core = build_core(config=cfg, llm=llm, repository=repo)

    session = core.start_session()
    assert core.reply("hi", session).reply == "ok"
    # The canon (system prompt) was loaded from the configured path — assert the ACTUAL canon content
    # rides a system prompt (persona-neutral: no hard-coded name, survives a canon rewrite). build_core
    # also loads the natal seed → a daily mood call runs first; the canon rides the turn call.
    from core.prompt import load_canon

    canon_head = load_canon(cfg.canon_path).strip()[:40]
    assert any(canon_head in c["system"] for c in llm.calls)


def test_reply_records_per_stage_timing(tmp_path):
    # S0 (LATENCY): a turn populates last_turn_timing with the PRE / MODEL / POST split.
    core, _ = _core_with(tmp_path, MockLLMClient("ок"))
    core.reply("привіт", core.start_session())
    t = core.last_turn_timing
    assert set(t) == {"pre_ms", "llm_ms", "post_ms", "think_chars", "ttft_ms", "total_ms"}
    assert all(isinstance(t[k], int) and t[k] >= 0 for k in ("pre_ms", "llm_ms", "post_ms", "think_chars"))
    assert t["total_ms"] == t["pre_ms"] + t["llm_ms"] + t["post_ms"]  # the split sums to the total
    assert t["ttft_ms"] is None  # v1.4: a blocking turn (no on_delta) never streamed a symbol


def test_latency_summary_last_and_median(tmp_path):
    # /latency reads latency_summary(): None before any turn, then last + median over the window.
    core, _ = _core_with(tmp_path, MockLLMClient("ок"))
    assert core.latency_summary() is None                       # nothing yet
    s = core.start_session()
    core.reply("а", s)
    core.reply("б", s)
    summ = core.latency_summary()
    assert summ["n"] == 2                                        # two turns in the rolling window
    assert summ["last"] == core.last_turn_timing
    assert set(summ["median"]) == {"pre_ms", "llm_ms", "post_ms", "total_ms"}

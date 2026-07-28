"""v1.6.1 LUMI-196 — Core.prompt_snapshot(): the assembled system prompt without a turn."""
from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from core.repository import LongTermFact
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 7, 28, 12, 0, tzinfo=UTC))


def _core(tmp_path, llm=None):
    repo = JsonRepository(tmp_path / "store.json")
    return Core(llm=llm or MockLLMClient("ок"), repository=repo,
                canon="Ти — Лілі, жива співрозмовниця.", model="m", clock=_CLK), repo


def test_snapshot_contains_canon_and_memory_sections(tmp_path):
    core, repo = _core(tmp_path)
    repo.add_fact(LongTermFact(user_id="owner", fact="любить каву вранці", meta="",
                               confidence=0.9, ts="2026-07-27T10:00:00+00:00", core=True))
    core2, _ = Core(llm=core._llm, repository=repo, canon="Ти — Лілі, жива співрозмовниця.",
                    model="m", clock=_CLK), repo  # facts inject at construction
    session = core2.start_session()
    snap = core2.prompt_snapshot(session)
    assert "Ти — Лілі, жива співрозмовниця." in snap    # the canon rides the snapshot
    assert "любить каву" in snap                        # the injected memory/facts section is there


def test_snapshot_makes_no_model_call_and_persists_nothing(tmp_path):
    llm = MockLLMClient("ок")
    core, repo = _core(tmp_path, llm)
    session = core.start_session()
    core.prompt_snapshot(session)
    assert llm.calls == []                              # no reply-model call (mood is off — no natal)
    assert repo.load_messages(session.id) == []          # nothing persisted


def test_snapshot_is_stable_across_calls(tmp_path):
    core, _ = _core(tmp_path)
    session = core.start_session()
    assert core.prompt_snapshot(session) == core.prompt_snapshot(session)


def test_snapshot_matches_the_reply_turn_prompt_head(tmp_path):
    # The snapshot must be the SAME builder a reply turn uses — compare against last_prompt's system
    # (the turn adds only the per-message RAG blocks, absent here because recall is off).
    core, _ = _core(tmp_path)
    session = core.start_session()
    snap = core.prompt_snapshot(session)
    core.reply("привіт", session)
    assert core.last_prompt["system"] == snap           # identical assembly (no recall configured)

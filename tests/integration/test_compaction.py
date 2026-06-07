"""In-session compaction — older-than-window messages folded into a digest (0.2.1)."""

from core.agent import Core
from core.llm import MockLLMClient
from core.memory import COMPACTION_DIGEST_SYSTEM
from core.repository import SessionDigest
from state.local_store import JsonRepository


def scripted(system, messages, model):
    """Digest calls return a digest; everything else is a normal turn reply."""
    return "DIGEST" if COMPACTION_DIGEST_SYSTEM in system else "ok"


def _core(tmp_path, window=2, batch=2):
    return Core(
        llm=MockLLMClient(scripted),
        repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.",
        model="m",
        memory_window=window,
        compaction_batch=batch,
    )


def test_digest_persists_and_reloads(tmp_path):
    path = tmp_path / "store.json"
    repo = JsonRepository(path)
    repo.set_digest(SessionDigest("s1", "earlier gist", 8, "2026-06-07T00:00:00+00:00"))
    reopened = JsonRepository(path)
    d = reopened.get_digest("s1")
    assert d is not None
    assert d.summary == "earlier gist" and d.compacted_count == 8
    assert reopened.get_digest("missing") is None


def test_short_session_is_not_compacted(tmp_path):
    core = _core(tmp_path, window=40, batch=20)  # defaults
    session = core.start_session()
    core.reply("привіт", session)
    assert core.last_compaction == 0
    assert core._repo.get_digest(session.id) is None  # no digest for a tiny session


def test_long_session_compacts_and_bounds_the_live_tail(tmp_path):
    core = _core(tmp_path, window=2, batch=2)  # compaction fires early
    session = core.start_session()
    for t in ("a", "b", "c", "d", "e", "f"):
        core.reply(t, session)

    # A digest was produced and covers the oldest messages.
    digest = core._repo.get_digest(session.id)
    assert digest is not None
    assert digest.summary == "DIGEST"
    assert digest.compacted_count >= 2

    # Every *turn* call sent at most window + batch verbatim messages (+ new line).
    turn_calls = [c for c in core._llm.calls if COMPACTION_DIGEST_SYSTEM not in c["system"]]
    assert all(len(c["messages"]) <= 2 + 2 + 1 for c in turn_calls)
    # Full history is still persisted (nothing lost from storage).
    assert len(core._repo.load_messages(session.id)) == 12


def test_digest_is_injected_into_the_system_prompt(tmp_path):
    core = _core(tmp_path, window=2, batch=2)
    session = core.start_session()
    for t in ("a", "b", "c", "d"):  # enough to trigger at least one compaction
        core.reply(t, session)
    # The last turn's system prompt carries the compacted digest block.
    system = core.last_prompt["system"]
    assert "Раніше в цій розмові" in system
    assert "DIGEST" in system


def test_last_compaction_reports_how_many_folded(tmp_path):
    core = _core(tmp_path, window=2, batch=2)
    session = core.start_session()
    core.reply("a", session)  # history 0 → no compaction
    assert core.last_compaction == 0
    core.reply("b", session)  # history 2, live 2 < 4 → no compaction
    assert core.last_compaction == 0
    core.reply("c", session)  # history 4 → fold 2
    assert core.last_compaction == 2

"""Memory commands — core view/clear + TUI /memory and /forget (LUMI-012)."""

from core.agent import Core, MemoryView
from core.llm import MockLLMClient
from core.repository import LongTermFact, ShortSummary
from state.local_store import JsonRepository
from tui.app import CANCELLED_LINE, CLEARED_LINE, LumiApp


def _seeded_repo(tmp_path, user_id="owner"):
    repo = JsonRepository(tmp_path / "store.json")
    repo.add_summary(ShortSummary(user_id, "s1", "Говорили про гори.", "2026-06-06T10:00:00+00:00"))
    repo.add_fact(LongTermFact(user_id, "Любить каву", "", 0.5, "2026-06-06T10:00:00+00:00"))
    return repo


def _core(repo, llm=None, user_id="owner"):
    return Core(
        llm=llm or MockLLMClient("ok"),
        repository=repo,
        canon="Ти — Лілі.",
        model="m",
        user_id=user_id,
    )


# --- core commands ---------------------------------------------------------
def test_view_memory_returns_snapshot(tmp_path):
    core = _core(_seeded_repo(tmp_path))
    mem = core.view_memory()
    assert isinstance(mem, MemoryView)
    assert mem.facts == ["Любить каву"]
    assert mem.summaries == ["Говорили про гори."]


def test_clear_memory_wipes_short_and_long_term_for_the_user(tmp_path):
    repo = _seeded_repo(tmp_path)
    core = _core(repo)
    core.clear_memory()
    assert core.view_memory().summaries == []
    assert core.view_memory().facts == []


def test_clear_memory_only_affects_the_active_user(tmp_path):
    repo = _seeded_repo(tmp_path, user_id="owner")
    repo.add_fact(LongTermFact("alice", "Alice fact", "", 0.5, "2026-06-06T10:00:00+00:00"))
    _core(repo, user_id="owner").clear_memory()
    # Alice's memory is untouched (isolation).
    assert [f.fact for f in repo.facts("alice")] == ["Alice fact"]


# --- TUI commands ----------------------------------------------------------
async def test_memory_command_shows_facts_and_summaries(tmp_path):
    app = LumiApp(_core(_seeded_repo(tmp_path)))
    async with app.run_test() as pilot:
        app.query_one("#prompt").text = "/memory"
        await pilot.press("enter")
        await pilot.pause()
        joined = "\n".join(app.transcript)
        assert "Любить каву" in joined
        assert "Говорили про гори." in joined
        # The command itself is not echoed as a user turn.
        assert "You: /memory" not in app.transcript


async def test_forget_command_clears_after_confirm(tmp_path):
    repo = _seeded_repo(tmp_path)
    core = _core(repo)
    app = LumiApp(core)
    async with app.run_test() as pilot:
        app.query_one("#prompt").text = "/forget"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("y")  # confirm
        await pilot.pause()
        assert core.view_memory().summaries == []
        assert core.view_memory().facts == []
        assert any(CLEARED_LINE in line for line in app.transcript)


async def test_forget_command_cancelled_keeps_memory(tmp_path):
    repo = _seeded_repo(tmp_path)
    core = _core(repo)
    app = LumiApp(core)
    async with app.run_test() as pilot:
        app.query_one("#prompt").text = "/forget"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("n")  # cancel
        await pilot.pause()
        assert core.view_memory().facts == ["Любить каву"]
        assert any(CANCELLED_LINE in line for line in app.transcript)

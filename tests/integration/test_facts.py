"""Long-term fact accumulation (LUMI-010)."""

from core.agent import Core
from core.llm import MockLLMClient
from core.memory import FACTS_SYSTEM, SUMMARY_SYSTEM, parse_facts
from core.repository import LongTermFact
from state.local_store import JsonRepository


def scripted(*, turn="вітаю", summary="підсумок", facts=""):
    """A mock model that answers by which internal call is being made."""

    def fn(system, messages, model):
        if system == SUMMARY_SYSTEM:
            return summary
        if system == FACTS_SYSTEM:
            return facts
        return turn

    return fn


def _core(tmp_path, llm, user_id="owner"):
    return Core(
        llm=llm,
        repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.",
        model="m",
        user_id=user_id,
    )


# --- parse_facts (pure) ----------------------------------------------------
def test_parse_facts_strips_bullets_and_blanks():
    text = "- Любить каву\n2. Грає на гітарі\n\n  • Зі Львова  \n"
    assert parse_facts(text) == ["Любить каву", "Грає на гітарі", "Зі Львова"]


def test_parse_facts_empty():
    assert parse_facts("") == []
    assert parse_facts("\n  \n") == []


# --- accumulation through end_session -------------------------------------
def test_end_session_accumulates_facts(tmp_path):
    core = _core(tmp_path, MockLLMClient(scripted(facts="Любить каву\nЗі Львова")))
    session = core.start_session()
    core.reply("привіт", session)
    core.end_session(session)

    facts = [f.fact for f in core._repo.facts("owner")]
    assert facts == ["Любить каву", "Зі Львова"]
    stored = core._repo.facts("owner")[0]
    assert isinstance(stored, LongTermFact)
    assert stored.user_id == "owner"
    assert stored.confidence == 0.5


def test_facts_accumulate_across_sessions_and_dedup(tmp_path):
    repo = JsonRepository(tmp_path / "store.json")

    def core_for(facts):
        return Core(llm=MockLLMClient(scripted(facts=facts)), repository=repo,
                    canon="Ти — Лілі.", model="m", user_id="owner")

    c1 = core_for("Любить каву")
    s1 = c1.start_session()
    c1.reply("a", s1)
    c1.end_session(s1)

    c2 = core_for("Любить каву\nГрає на гітарі")  # one repeat, one new
    s2 = c2.start_session()
    c2.reply("b", s2)
    c2.end_session(s2)

    facts = [f.fact for f in repo.facts("owner")]
    assert facts == ["Любить каву", "Грає на гітарі"]  # deduped, accumulated


def test_facts_are_isolated_by_user(tmp_path):
    repo = JsonRepository(tmp_path / "store.json")
    repo.add_fact(LongTermFact("alice", "Alice loves tea", "", 0.5, "2026-06-06T10:00:00+00:00"))
    assert [f.fact for f in repo.facts("alice")] == ["Alice loves tea"]
    assert repo.facts("bob") == []  # isolation invariant


def test_no_facts_when_model_returns_nothing(tmp_path):
    core = _core(tmp_path, MockLLMClient(scripted(facts="")))
    session = core.start_session()
    core.reply("привіт", session)
    core.end_session(session)
    assert core._repo.facts("owner") == []

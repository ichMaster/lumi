"""Startup rehydration — a restart recalls the user's context (LUMI-011)."""

from core.agent import Core
from core.llm import MockLLMClient
from core.memory import FACTS_SYSTEM, SUMMARY_SYSTEM
from state.local_store import JsonRepository


def scripted(*, turn="вітаю", summary="підсумок", facts=""):
    def fn(system, messages, model):
        if SUMMARY_SYSTEM in system:
            return summary
        if FACTS_SYSTEM in system:
            return facts
        return turn

    return fn


def test_restart_rehydrates_summary_and_facts_into_the_prompt(tmp_path):
    path = tmp_path / "store.json"

    # --- Session 1: chat, then end → writes a summary + a fact.
    llm1 = MockLLMClient(
        scripted(
            turn="Радо знайомлюся.",
            summary="Перша розмова: знайомство, говорили про каву.",
            facts="Любить каву",
        )
    )
    core1 = Core(llm=llm1, repository=JsonRepository(path), canon="Ти — Лілі.", model="m")
    s1 = core1.start_session()
    core1.reply("привіт, я Сашко", s1)
    core1.end_session(s1)

    # --- Restart: a fresh Core + store over the same file.
    llm2 = MockLLMClient("Привіт ще раз!")
    core2 = Core(llm=llm2, repository=JsonRepository(path), canon="Ти — Лілі.", model="m")
    s2 = core2.start_session()
    core2.reply("я повернувся", s2)

    # The model call carries the rehydrated memory in its system prompt.
    system = llm2.calls[-1]["system"]
    assert "Ти — Лілі." in system
    assert "Перша розмова: знайомство, говорили про каву." in system
    assert "Любить каву" in system


def test_fresh_user_has_no_memory_in_prompt(tmp_path):
    llm = MockLLMClient("привіт")
    core = Core(llm=llm, repository=JsonRepository(tmp_path / "s.json"), canon="Ти — Лілі.", model="m")
    session = core.start_session()
    core.reply("привіт", session)
    # No prior sessions → no memory blocks; the system is the canon (+ the fixed
    # answer-only directive), with nothing user-specific.
    system = llm.calls[-1]["system"]
    assert system.startswith("Ти — Лілі.")
    assert "# Памʼять про цю людину" not in system  # no memory section with nothing to recall

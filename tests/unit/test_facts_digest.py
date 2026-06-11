"""Facts digest — consolidate long-term facts into a compact prompt view (non-destructive).

One housekeeping call rebuilds the digest only when the raw facts grow past it; the prompt then
injects the digest (+ a verbatim tail of facts added since) instead of all raw facts. Raw facts
are never modified. Mock model — no network, no paid calls.
"""

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from core.memory import facts_digest_request
from core.repository import FactsDigest, LongTermFact
from state.local_store import JsonRepository

NOW = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)


def _repo_with_facts(path, n, user="owner"):
    repo = JsonRepository(path)
    for i in range(n):
        repo.add_fact(LongTermFact(user, f"факт номер {i}", "", 1.0, NOW.isoformat()))
    return repo


def _core(repo, llm, **kw):
    return Core(
        llm=llm, repository=repo, canon="C", model="m", clock=fixed_clock(NOW),
        mood_enabled=False, biorhythms_enabled=False, cycle_enabled=False,
        closeness_enabled=False, thoughts_enabled=False, **kw,
    )


# --- the request builder ---------------------------------------------------
def test_facts_digest_request_builds_prompt():
    system, msgs = facts_digest_request(["a", "b", "c"], 150)
    assert "150" in system                       # the target rides in the instruction
    assert msgs[0]["role"] == "user"
    assert "- a" in msgs[0]["content"] and "- c" in msgs[0]["content"]


# --- rebuild / reuse logic -------------------------------------------------
def test_builds_digest_when_facts_exceed_max(tmp_path):
    repo = _repo_with_facts(tmp_path / "s.json", 12)
    llm = MockLLMClient(replies="- зведений факт 1\n- зведений факт 2")
    core = _core(repo, llm, facts_digest_enabled=True, facts_digest_max=5, facts_digest_refresh=3)
    assert repo.get_facts_digest("owner") is None
    core._ensure_facts_digest()
    d = repo.get_facts_digest("owner")
    assert d is not None and d.count == 12                       # built from all 12 raw facts
    assert "зведений факт 1" in d.summary and "зведений факт 2" in d.summary
    assert len(repo.facts("owner")) == 12                        # raw facts untouched (non-destructive)


def test_skips_when_facts_below_max(tmp_path):
    repo = _repo_with_facts(tmp_path / "s.json", 4)
    llm = MockLLMClient(replies="- x")
    core = _core(repo, llm, facts_digest_enabled=True, facts_digest_max=5)
    core._ensure_facts_digest()
    assert repo.get_facts_digest("owner") is None and llm.calls == []  # 4 ≤ 5 → no digest, no call


def test_reuses_digest_when_fresh(tmp_path):
    repo = _repo_with_facts(tmp_path / "s.json", 12)
    llm = MockLLMClient(replies="- digest")
    core = _core(repo, llm, facts_digest_enabled=True, facts_digest_max=5, facts_digest_refresh=5)
    core._ensure_facts_digest()
    assert len(llm.calls) == 1
    repo.add_fact(LongTermFact("owner", "новий", "", 1.0, NOW.isoformat()))  # 13 — grew 1 < 5
    core._ensure_facts_digest()
    assert len(llm.calls) == 1                                   # no rebuild — reused
    assert repo.get_facts_digest("owner").count == 12


def test_rebuilds_when_grown_past_refresh(tmp_path):
    repo = _repo_with_facts(tmp_path / "s.json", 12)
    llm = MockLLMClient(replies="- digest")
    core = _core(repo, llm, facts_digest_enabled=True, facts_digest_max=5, facts_digest_refresh=3)
    core._ensure_facts_digest()
    for i in range(3):
        repo.add_fact(LongTermFact("owner", f"n{i}", "", 1.0, NOW.isoformat()))  # 15 — grew 3 ≥ 3
    core._ensure_facts_digest()
    assert len(llm.calls) == 2                                   # rebuilt
    assert repo.get_facts_digest("owner").count == 15


def test_disabled_is_a_noop(tmp_path):
    repo = _repo_with_facts(tmp_path / "s.json", 20)
    llm = MockLLMClient(replies="- d")
    core = _core(repo, llm, facts_digest_enabled=False, facts_digest_max=5)
    core._ensure_facts_digest()
    assert repo.get_facts_digest("owner") is None and llm.calls == []


# --- the prompt uses the digest, not raw facts -----------------------------
def test_prompt_injects_digest_not_raw_facts(tmp_path):
    repo = _repo_with_facts(tmp_path / "s.json", 12)
    llm = MockLLMClient(
        replies="- ЗВЕДЕНО: одне",                               # the digest housekeeping output
        states={"reply": "ок", "emotion": "calm", "intensity": 0.4},
    )
    core = _core(repo, llm, facts_digest_enabled=True, facts_digest_max=5, facts_digest_refresh=3)
    core.reply("привіт", core.start_session())
    system = core.last_prompt["system"]
    assert "ЗВЕДЕНО: одне" in system                             # the digest is injected
    assert "факт номер 0" not in system                         # raw facts replaced by the digest
    assert len(repo.facts("owner")) >= 12                       # raw facts preserved in the store


# --- persistence -----------------------------------------------------------
def test_facts_digest_persists_and_clears(tmp_path):
    p = tmp_path / "s.json"
    JsonRepository(p).set_facts_digest(FactsDigest("owner", "- a\n- b", 10, NOW.isoformat()))
    d = JsonRepository(p).get_facts_digest("owner")             # fresh load from disk
    assert d is not None and d.count == 10 and "- a" in d.summary
    repo = JsonRepository(p)
    repo.clear_memory("owner")
    assert JsonRepository(p).get_facts_digest("owner") is None  # /forget clears it too

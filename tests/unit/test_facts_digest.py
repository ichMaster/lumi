"""Facts digest — DORMANT seam.

The facts-digest *mode* (consolidate all raw facts into a compact prompt view) was retired: the
static facts block is now the skill-curated identity-core (`## Про Віталія`, `LUMI_FACTS`), with no
digest/raw fallback. The pure request builder (`facts_digest_request`) and the repository storage
(`FactsDigest` + `get/set_facts_digest`, cleared by `/forget`) remain as an unused seam; these two
tests pin that they still work. Mock model — no network, no paid calls.
"""

from datetime import UTC, datetime

from core.memory import facts_digest_request
from core.repository import FactsDigest
from state.local_store import JsonRepository

NOW = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)


def test_facts_digest_request_builds_prompt():
    system, msgs = facts_digest_request(["a", "b", "c"], 150)
    assert "150" in system                       # the target rides in the instruction
    assert msgs[0]["role"] == "user"
    assert "- a" in msgs[0]["content"] and "- c" in msgs[0]["content"]


def test_facts_digest_persists_and_clears(tmp_path):
    p = tmp_path / "s.json"
    JsonRepository(p).set_facts_digest(FactsDigest("owner", "- a\n- b", 10, NOW.isoformat()))
    d = JsonRepository(p).get_facts_digest("owner")             # fresh load from disk
    assert d is not None and d.count == 10 and "- a" in d.summary
    repo = JsonRepository(p)
    repo.clear_memory("owner")
    assert JsonRepository(p).get_facts_digest("owner") is None  # /forget clears it too

"""v1.5 LUMI-195 — the full fast-turn path: async POST + SQLite backend + the migration script."""
from __future__ import annotations

import threading
from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from state.local_store import JsonRepository
from state.sqlite_store import SqliteRepository

_CLK = fixed_clock(datetime(2026, 7, 21, 12, 0, tzinfo=UTC))


class _GatedSqliteRepo(SqliteRepository):
    """append_message blocks until the gate opens — the delayed-persist simulation on SQLite."""

    def __init__(self, path):
        super().__init__(path)
        self.gate = threading.Event()

    def append_message(self, message):
        self.gate.wait(timeout=5)
        super().append_message(message)


def test_full_fast_turn_returns_before_persist_and_lands_in_sqlite(tmp_path):
    # The whole v1.5 stack: LUMI_ASYNC_POST=on + LUMI_STORE_BACKEND=sqlite.
    repo = _GatedSqliteRepo(tmp_path / "store.json")
    core = Core(llm=MockLLMClient(["перша", "друга"]), repository=repo,
                canon="Ти — Лілі.", model="m", clock=_CLK, async_post=True)
    session = core.start_session()
    state = core.reply("привіт", session)
    assert state.reply == "перша"                            # returned…
    assert repo.load_messages(session.id) == []               # …before the gated persist
    repo.gate.set()
    core.reply("ще", session)                                 # next turn drains first (ordering)
    core.drain_post()                                         # settle the second turn's own tail too
    msgs = repo.load_messages(session.id)
    assert [m.text for m in msgs] == ["привіт", "перша", "ще", "друга"]  # all landed, in order
    size = (tmp_path / "store.json").stat().st_size
    core.reply("і ще", session)
    repo.gate.set()
    core.drain_post()
    assert (tmp_path / "store.json").stat().st_size == size   # O(1): the JSON never grew per message


def test_abort_on_sqlite_loses_at_most_the_undrained_turn(tmp_path):
    repo = _GatedSqliteRepo(tmp_path / "store.json")
    core = Core(llm=MockLLMClient("ок"), repository=repo,
                canon="Ти — Лілі.", model="m", clock=_CLK, async_post=True)
    session = core.start_session()
    core.reply("привіт", session)                             # queued, never drained → "abort"
    fresh = SqliteRepository(tmp_path / "store.json")         # next start
    assert fresh.load_messages(session.id) == []              # that turn lost…
    assert fresh.get_session(session.id) is not None          # …store + DB intact


def test_migration_script_round_trip(tmp_path, monkeypatch):
    # JSON store with history + vectors → migrate → identical reads; idempotent; export rolls back.
    from core.repository import VectorRecord
    from scripts.migrate_store import export_json, migrate

    store = tmp_path / "store.json"
    old = JsonRepository(store)
    s = old.create_session("owner")
    from tests.contract.test_store_backends import _msg
    old.append_message(_msg(s.id, "owner", "user", "історія"))
    old.append_message(_msg(s.id, "owner", "lili", "відповідь", emotion="calm", intensity=0.5))
    old.add_vector(VectorRecord(user_id="owner", msg_id="m1", vector=[0.6, 0.8], text="історія",
                                ts="2026-07-21T10:00:00+00:00", role="user"))

    migrate(store)                                            # forward (creates a backup itself)
    repo = SqliteRepository(store)
    assert [m.text for m in repo.load_messages(s.id)] == ["історія", "відповідь"]
    hits = repo.search_vectors("owner", [0.6, 0.8], 1)
    assert hits and hits[0][1].msg_id == "m1"                 # vectors re-packed, same result

    migrate(store)                                            # idempotent second run
    assert len(SqliteRepository(store).load_messages(s.id)) == 2

    export_json(store)                                        # rollback
    back = JsonRepository(store)
    assert [m.text for m in back.load_messages(s.id)] == ["історія", "відповідь"]
    assert back.search_vectors("owner", [0.6, 0.8], 1)[0][1].msg_id == "m1"


def test_off_pins_defaults_are_inert(tmp_path):
    # LUMI_ASYNC_POST off + json backend (defaults) → the classic synchronous JSON path.
    from core.config import load_config
    cfg = load_config(load_env=False)
    assert cfg.async_post is False and cfg.store_backend == "json"
    repo = JsonRepository(tmp_path / "store.json")
    core = Core(llm=MockLLMClient("ок"), repository=repo, canon="Ти — Лілі.", model="m", clock=_CLK)
    session = core.start_session()
    core.reply("привіт", session)
    assert len(repo.load_messages(session.id)) == 2           # persisted synchronously
    assert core._post_q is None                                # no worker ever started

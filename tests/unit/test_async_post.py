"""v1.5 LUMI-192 — the async post-turn queue (S1): FIFO, drain points, retry, off-pin."""
from __future__ import annotations

import threading
from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 7, 21, 12, 0, tzinfo=UTC))


def _core(tmp_path, llm=None, *, async_post: bool, repo=None):
    repo = repo or JsonRepository(tmp_path / "store.json")
    return Core(
        llm=llm or MockLLMClient("ок"),
        repository=repo,
        canon="Ти — Лілі.",
        model="m",
        clock=_CLK,
        async_post=async_post,
    ), repo


class _GatedRepo(JsonRepository):
    """append_message blocks until the gate opens — simulates a slow persist."""

    def __init__(self, path):
        super().__init__(path)
        self.gate = threading.Event()

    def append_message(self, message):
        self.gate.wait(timeout=5)
        super().append_message(message)


class _FlakyRepo(JsonRepository):
    """The FIRST append_message raises; everything after succeeds (retry-once semantics)."""

    def __init__(self, path):
        super().__init__(path)
        self.failed = False

    def append_message(self, message):
        if not self.failed:
            self.failed = True
            raise RuntimeError("disk hiccup")
        super().append_message(message)


def test_queue_runs_jobs_fifo_and_drain_empties(tmp_path):
    core, _ = _core(tmp_path, async_post=True)
    ran: list[int] = []
    for i in range(5):
        core._post_enqueue(lambda i=i: ran.append(i))
    core.drain_post()
    assert ran == [0, 1, 2, 3, 4]  # strict FIFO, fully drained


def test_reply_returns_before_the_persist_lands(tmp_path):
    # The S1 core promise: reply() returns while the (gated) persist is still pending.
    repo = _GatedRepo(tmp_path / "store.json")
    core, _ = _core(tmp_path, async_post=True, repo=repo)
    session = core.start_session()
    state = core.reply("привіт", session)
    assert state.reply == "ок"                          # returned…
    assert repo.load_messages(session.id) == []          # …before anything persisted
    repo.gate.set()
    core.drain_post()
    msgs = repo.load_messages(session.id)
    assert [m.role for m in msgs] == ["user", "lili"]    # then both records landed, in order


def test_next_turn_prompt_carries_the_prior_turn(tmp_path):
    # Drain-at-prompt-build: the second turn's payload includes the first turn's (queued) messages.
    llm = MockLLMClient(["перша", "друга"])
    core, _ = _core(tmp_path, llm, async_post=True)
    session = core.start_session()
    core.reply("раз", session)
    core.reply("два", session)
    second = llm.calls[-1]["messages"]
    joined = " ".join(str(m["content"]) for m in second)
    assert "раз" in joined and "перша" in joined         # prior turn present (drained before build)


def test_session_close_drains_fully(tmp_path):
    repo = _GatedRepo(tmp_path / "store.json")
    core, _ = _core(tmp_path, async_post=True, repo=repo)
    session = core.start_session()
    core.reply("привіт", session)
    repo.gate.set()
    core.end_session(session)                            # close drains before summarizing
    assert len(repo.load_messages(session.id)) == 2


def test_failed_job_is_retried_once_on_drain(tmp_path):
    repo = _FlakyRepo(tmp_path / "store.json")
    core, _ = _core(tmp_path, async_post=True, repo=repo)
    session = core.start_session()
    core.reply("привіт", session)
    core.drain_post()                                    # first run fails → retried here
    assert len(repo.load_messages(session.id)) == 2      # the retry persisted the turn


def test_abort_loses_at_most_the_undrained_turn(tmp_path):
    # Simulated abort: the gate never opens, the process "dies" — a fresh start still loads cleanly.
    path = tmp_path / "store.json"
    repo = _GatedRepo(path)
    core, _ = _core(tmp_path, async_post=True, repo=repo)
    session = core.start_session()
    core.reply("привіт", session)                        # queued, never drained
    fresh = JsonRepository(path)                         # next start
    assert fresh.load_messages(session.id) == []         # that turn is lost…
    assert fresh.get_session(session.id) is not None     # …but the store is intact and loads


def test_off_default_is_synchronous_and_never_starts_a_worker(tmp_path):
    core, repo = _core(tmp_path, async_post=False)
    session = core.start_session()
    core.reply("привіт", session)
    assert len(repo.load_messages(session.id)) == 2      # persisted before return (sync)
    assert core._post_q is None                           # the worker never started (byte-identical path)

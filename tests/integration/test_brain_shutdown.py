"""Code review v2.5 — the brain host's lifecycle and the last background model call.

#1: after the stop the host starts no new act (it used to start the next drained Telegram line on the closed
session), and an inbox line it never reached waits for the next start instead of being lost (the pointer moves
per line, after its turn). #2: the startup mood is the server's own act — never beside a turn. #3: the idle
think/nudge read their idle anchor inside the lock. Driven interleavings; mock model; no network.
"""

import dataclasses
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime

from core.agent import Core
from core.config import load_config
from core.llm import MockLLMClient
from server.app import create_app
from server.brain import BrainHost
from state import fifo
from state.local_store import JsonRepository

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def _setup(tmp_path, llm=None, **cfg_kw):
    core = Core(llm=llm or MockLLMClient("думка\nЕМОЦІЯ: calm", states={"reply": "так", "emotion": "calm",
                                                                       "intensity": 0.5}),
                repository=JsonRepository(tmp_path / "s.json"), canon="Ти — Лілі.", model="m",
                clock=lambda: NOW, mood_enabled=False)
    app = create_app(core, token="t", env="dev", version="2.5.0")
    settings = {"bridge": True, "inbox_path": tmp_path / "inbox.jsonl", "outbox_path": tmp_path / "outbox.jsonl",
                "scheduler": False, "thoughts": False, "idle_nudge": False,
                "schedule_state_path": tmp_path / "schedule.state"}
    cfg = dataclasses.replace(load_config(load_env=False), **{**settings, **cfg_kw})
    return app, core, cfg


# --- #1: no act after the stop; an unreached line waits ---------------------------------------------------

def test_a_stop_during_a_burst_leaves_the_rest_for_the_next_start(tmp_path):
    holder: dict = {}

    def reply_then_ctrl_c(system, messages, model):
        holder["host"].request_stop()  # Ctrl+C lands while the first line is being answered
        return {"reply": "відповідь", "emotion": "calm", "intensity": 0.5}

    app, core, cfg = _setup(tmp_path, MockLLMClient(states=reply_then_ctrl_c))
    for text in ("раз", "два", "три"):
        fifo.append(tmp_path / "inbox.jsonl", text, source="telegram")
    host = holder["host"] = BrainHost(app.state.service, cfg, clock=lambda: NOW)
    host.inbox_tick(NOW)
    assert core.totals.turns == 1  # no new act after the stop
    assert fifo.load_pointer(tmp_path / "inbox.pos") == 1  # only the answered line is acknowledged
    restarted = BrainHost(app.state.service, cfg, clock=lambda: NOW)
    holder["host"] = restarted
    restarted.inbox_tick(NOW)  # the next start picks up where it stopped…
    assert core.totals.turns == 2 and fifo.load_pointer(tmp_path / "inbox.pos") == 2  # …line by line


def test_a_stop_between_due_entries_starts_no_further_act(tmp_path):
    schedule = tmp_path / "schedule.toml"
    schedule.write_text('[[schedule]]\ndirective = "think"\nevery = "10m"\n\n'
                        '[[schedule]]\ndirective = "wonder"\nevery = "10m"\n', encoding="utf-8")
    app, core, cfg = _setup(tmp_path, scheduler=True, schedule_path=schedule, bridge=False)
    host = BrainHost(app.state.service, cfg, clock=lambda: NOW)
    real = core.run_directive

    def first_then_ctrl_c(*a, **kw):
        host.request_stop()
        return real(*a, **kw)

    core.run_directive = first_then_ctrl_c
    host.scheduler_tick(NOW)
    assert len(core._repo.thoughts_since("2000-01-01")) == 1  # the second due entry never started


def test_the_first_ctrl_c_also_asks_the_brain_to_stop(tmp_path):
    import signal

    from server.__main__ import make_server

    app, core, cfg = _setup(tmp_path)
    host = app.state.brain = BrainHost(app.state.service, cfg, clock=lambda: NOW)
    server = make_server(app, host="127.0.0.1", port=0)
    server.handle_exit(signal.SIGINT, None)
    deadline = time.monotonic() + 2
    while not host._stop.is_set():
        assert time.monotonic() < deadline
        time.sleep(0.01)


# --- #2: the startup mood never runs beside a turn ----------------------------------------------------------

def test_the_startup_mood_is_the_servers_own_act(tmp_path):
    from server.__main__ import startup_mood

    app, core, cfg = _setup(tmp_path)
    service = app.state.service
    seen: list = []
    core.ensure_mood = lambda: seen.append(service._bg)  # what the lock looked like while the mood ran
    service.lock.acquire()  # a client turn is running
    t = threading.Thread(target=startup_mood, args=(service,))
    t.start()
    time.sleep(0.1)
    assert seen == []  # it waited for the turn…
    service.lock.release()
    t.join(2)
    assert seen == [True]  # …then ran holding the lock as the server's own act


# --- #3: the idle anchor is read inside the lock ------------------------------------------------------------

def _turn_finishes_while_waiting(service):
    real = service.background

    @contextmanager
    def background():
        with real():
            service.last_input = NOW  # a client turn that held the lock just finished — you just spoke
            yield

    service.background = background


def test_the_idle_think_reads_a_fresh_anchor(tmp_path):
    app, core, cfg = _setup(tmp_path, thoughts=True, bridge=False)
    core._thoughts_interval_s = 60
    service = app.state.service
    service.last_input = datetime(2026, 10, 11, 11, 0, tzinfo=UTC)  # silent for an hour…
    host = BrainHost(service, cfg, clock=lambda: NOW)
    host._last_think = service.last_input
    _turn_finishes_while_waiting(service)
    host.idle_tick(NOW)
    assert core._repo.thoughts_since("2000-01-01") == []  # …but you just spoke: no spontaneous thought


def test_the_idle_nudge_reads_a_fresh_anchor(tmp_path):
    nudges = tmp_path / "nudges.md"
    nudges.write_text("Привіт, ти тут?\n", encoding="utf-8")
    app, core, cfg = _setup(tmp_path, idle_nudge=True, nudge_path=nudges, idle_seconds=60, bridge=False)
    service = app.state.service
    service.last_input = datetime(2026, 10, 11, 11, 0, tzinfo=UTC)
    host = BrainHost(service, cfg, clock=lambda: NOW)
    host._last_nudge = service.last_input
    _turn_finishes_while_waiting(service)
    host.idle_tick(NOW)
    assert core.totals.turns == 0  # no opener right after you spoke

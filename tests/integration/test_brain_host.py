"""The brain host (v2.5 LUMI-224) — her scheduled and idle acts run in the server, client or not.

The scheduler contracts re-run against the server host (a fake clock, a mock model): due entries fire once,
quiet hours veto, day caps hold, the startup catch-up fires a missed ``at:`` once; loud thoughts are pushed,
graduating ones become spoken turns, silent ones are only recorded; every act takes the lock as the server's
own (a client request waits for it, it waits for a client turn). No network, no paid API.
"""

import dataclasses
import threading
import time
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from core.agent import Core
from core.config import load_config
from core.llm import MockLLMClient
from server.app import create_app
from server.brain import BrainHost
from state.local_store import JsonRepository

TOKEN = "brain-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
THOUGHT = "море шумить у тиші"


class _Clock:
    def __init__(self, when: datetime) -> None:
        self.now = when

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> datetime:
        self.now += timedelta(**kw)
        return self.now


def _setup(tmp_path, schedule: str = "", *, rng=lambda a, b: 99, **cfg_kw):
    clock = _Clock(datetime(2026, 10, 11, 12, 0, tzinfo=UTC))
    llm = MockLLMClient(f"{THOUGHT}\nЕМОЦІЯ: tender",
                        states={"reply": "Я тут подумала про море", "emotion": "tender", "intensity": 0.6})
    core = Core(llm=llm, repository=JsonRepository(tmp_path / "s.json"), canon="Ти — Лілі.", model="m",
                clock=clock, mood_enabled=False)
    app = create_app(core, token=TOKEN, env="dev", version="2.5.0")
    (tmp_path / "schedule.toml").write_text(schedule, encoding="utf-8")
    settings = {
        "scheduler": bool(schedule), "schedule_path": tmp_path / "schedule.toml",
        "schedule_state_path": tmp_path / "schedule.state", "sched_day_cap": 24, "thoughts_quiet_hours": None,
        "thoughts_spoken_ratio": 0.0, "thoughts": False, "idle_nudge": False, "quiet_hours": None,
    }
    cfg = dataclasses.replace(load_config(load_env=False), **{**settings, **cfg_kw})
    host = BrainHost(app.state.service, cfg, clock=clock, rng=rng)
    return host, app, core, clock


def _events(app) -> tuple:
    return app.state.service.bus.subscribe()


def _drain(sub) -> list:
    out = []
    while not sub.empty():
        out.append(sub.get_nowait())
    return out


EVERY = '[[schedule]]\ndirective = "think"\nevery = "10m"\nshow = {show}\n'


def test_a_due_entry_fires_once_server_side(tmp_path):
    host, app, core, clock = _setup(tmp_path, EVERY.format(show="false"))
    host.scheduler_tick(clock())  # first beat → due
    assert [t.text for t in core._repo.thoughts_since("2000-01-01")] == [THOUGHT]
    host.scheduler_tick(clock.advance(minutes=5))  # 5 of 10 minutes → not again
    assert len(core._repo.thoughts_since("2000-01-01")) == 1
    host.scheduler_tick(clock.advance(minutes=5))
    assert len(core._repo.thoughts_since("2000-01-01")) == 2


def test_a_silent_thought_is_recorded_but_never_pushed(tmp_path):
    host, app, core, clock = _setup(tmp_path, EVERY.format(show="false"))
    sub = _events(app)
    host.scheduler_tick(clock())
    assert [e for e, _ in _drain(sub) if e in ("thought", "message")] == []


def test_a_loud_thought_is_pushed_with_no_client_origin(tmp_path):
    host, app, core, clock = _setup(tmp_path, EVERY.format(show="true"))
    sub = _events(app)
    host.scheduler_tick(clock())
    thoughts = [d for e, d in _drain(sub) if e == "thought"]
    assert thoughts == [{"kind": "think", "text": THOUGHT, "emotion": "tender",
                         "when": thoughts[0]["when"], "origin": None}]


def test_a_graduating_thought_becomes_a_spoken_turn(tmp_path):
    host, app, core, clock = _setup(tmp_path, EVERY.format(show="true"), rng=lambda a, b: 0,
                                    thoughts_spoken_ratio=1.0)
    sub = _events(app)
    host.scheduler_tick(clock())
    events = _drain(sub)
    spoken = [d for e, d in events if e == "message"]
    assert spoken == [{"role": "lili", "text": "Я тут подумала про море", "source": "thought",
                       "emotion": "tender", "intensity": 0.6, "origin": None}]
    assert "turn" in [e for e, _ in events] and "thought" not in [e for e, _ in events]
    seed = core._llm.calls[-1]["messages"][-1]["content"]  # the hidden self-turn the model got
    assert THOUGHT in seed and "напиши йому перша" in seed
    assert all("напиши йому перша" not in d.get("text", "") for _, d in events)  # the seed is never pushed


def test_quiet_hours_veto_a_periodic_entry(tmp_path):
    host, app, core, clock = _setup(tmp_path, EVERY.format(show="true"))
    host._scheduler._quiet_hours = (0, 24)  # the whole day is quiet
    host.scheduler_tick(clock())
    assert core._repo.thoughts_since("2000-01-01") == []


def test_the_day_cap_holds(tmp_path):
    host, app, core, clock = _setup(tmp_path, EVERY.format(show="false"))
    host._scheduler._day_cap = host._scheduler._per_dir_cap = 1
    for _ in range(3):
        host.scheduler_tick(clock.advance(minutes=11))
    assert len(core._repo.thoughts_since("2000-01-01")) == 1


def test_the_startup_catch_up_fires_a_missed_at_once(tmp_path):
    host, app, core, clock = _setup(tmp_path, '[[schedule]]\ndirective = "think"\nat = "11:30"\n')
    host.catch_up(clock())  # 12:00 — the 11:30 was missed while the brain was down
    host.catch_up(clock.advance(minutes=1))  # only once
    assert len(core._repo.thoughts_since("2000-01-01")) == 1


def test_a_turn_in_flight_holds_the_scheduler_back(tmp_path):
    host, app, core, clock = _setup(tmp_path, EVERY.format(show="false"))
    service = app.state.service
    service.lock.acquire()  # a client turn is running
    try:
        host.scheduler_tick(clock())
    finally:
        service.lock.release()
    assert core._repo.thoughts_since("2000-01-01") == []  # skipped, not stamped…
    host.scheduler_tick(clock.advance(seconds=30))
    assert len(core._repo.thoughts_since("2000-01-01")) == 1  # …so it fires on the next beat


def test_a_client_request_waits_for_the_servers_own_act(tmp_path):
    host, app, core, clock = _setup(tmp_path)
    service = app.state.service
    client = TestClient(app)
    order: list[str] = []
    held = threading.Event()

    def act():
        with service.background():
            held.set()
            time.sleep(0.3)
            order.append("act done")

    threading.Thread(target=act).start()
    assert held.wait(2)
    res = client.post("/v1/command", json={"line": "/closeness"}, headers=AUTH)
    order.append("command")
    assert res.status_code == 200 and order == ["act done", "command"]  # waited — no 409, no interleave


def test_two_client_requests_still_409(tmp_path):
    host, app, core, clock = _setup(tmp_path)
    service = app.state.service
    service.lock.acquire()  # another client's request
    try:
        res = TestClient(app).post("/v1/command", json={"line": "/closeness"}, headers=AUTH)
    finally:
        service.lock.release()
    assert res.status_code == 409


def test_the_legacy_idle_think_runs_when_the_scheduler_is_off(tmp_path):
    host, app, core, clock = _setup(tmp_path, rng=lambda a, b: 0, thoughts=True, thoughts_spoken_ratio=1.0)
    core._thoughts_interval_s = 60
    sub = _events(app)
    host.idle_tick(clock())  # not idle long enough yet
    assert core._repo.thoughts_since("2000-01-01") == []
    host.idle_tick(clock.advance(minutes=2))
    assert len(core._repo.thoughts_since("2000-01-01")) == 1
    assert [d["source"] for e, d in _drain(sub) if e == "message"] == ["thought"]  # spoken (ratio 1)


def test_the_legacy_nudge_speaks_first_after_silence(tmp_path):
    nudges = tmp_path / "nudges.md"
    nudges.write_text("Привіт, ти тут?\n", encoding="utf-8")
    host, app, core, clock = _setup(tmp_path, idle_nudge=True, nudge_path=nudges, idle_seconds=60)
    sub = _events(app)
    host.idle_tick(clock.advance(minutes=2))
    msgs = [d for e, d in _drain(sub) if e == "message"]
    assert [m["source"] for m in msgs] == ["nudge"] and msgs[0]["role"] == "lili"
    assert core._llm.calls[-1]["messages"][-1]["content"].endswith("Привіт, ти тут?")  # the hidden opener


def test_a_client_turn_resets_the_idle_anchor(tmp_path):
    host, app, core, clock = _setup(tmp_path)
    clock.advance(minutes=5)
    TestClient(app).post("/v1/turn", json={"text": "привіт"}, headers=AUTH)
    assert app.state.service.last_input == clock()


def test_with_every_proactive_flag_off_nothing_is_hosted(tmp_path):
    host, app, core, clock = _setup(tmp_path)
    assert host.active is False and host.start() is False
    host.tick(clock.advance(hours=3))
    assert core._repo.thoughts_since("2000-01-01") == [] and core._llm.calls == []


def test_a_failing_act_never_stops_the_host(tmp_path):
    host, app, core, clock = _setup(tmp_path, EVERY.format(show="true"))
    real = core.run_directive

    def boom(*a, **kw):
        core.run_directive = real
        raise RuntimeError("tool exploded")

    core.run_directive = boom
    host.scheduler_tick(clock())  # logged, swallowed — and the lock is released
    assert not app.state.service.lock.locked()
    host.scheduler_tick(clock.advance(minutes=10))
    assert len(core._repo.thoughts_since("2000-01-01")) == 1


def test_start_runs_the_beat_on_a_thread_and_stop_ends_it(tmp_path, monkeypatch):
    import server.brain as brain

    monkeypatch.setattr(brain, "BEAT_S", 0.01)
    host, app, core, clock = _setup(tmp_path, EVERY.format(show="false"))
    assert host.start() is True
    deadline = time.monotonic() + 3
    while not core._repo.thoughts_since("2000-01-01"):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    host.stop()
    assert not host._thread.is_alive()

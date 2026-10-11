"""Telegram beside the server (v2.5 LUMI-225) — the file bus on the server, `message` events, the replay.

The brain host drains the inbox into turns and writes her replies to the outbox (never an inbox line back —
no echo); client turns mirror like in-process keyboard turns; every conversation line is a `message` event,
kept in a bounded replay a fresh listener gets first and a resuming one gets only the rest of. A mock model,
fake bus files under tmp; no network, no paid API.
"""

import dataclasses
import threading
import time
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from core.agent import Core
from core.config import load_config
from core.llm import MockLLMClient
from server.__main__ import outbox_of, telegram_sink_of
from server.app import EventBus, Outbox, create_app
from server.brain import BrainHost
from state import fifo
from state.local_store import JsonRepository

TOKEN = "bus-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ME = {**AUTH, "X-Lumi-Client": "tui-1"}
THOUGHT = "море шумить у тиші"


def _setup(tmp_path, *, mirror_user=True, schedule="", rng=lambda a, b: 99, **cfg_kw):
    now = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    core = Core(llm=MockLLMClient(f"{THOUGHT}\nЕМОЦІЯ: tender",
                                  states={"reply": "Я тут, привіт!", "emotion": "joy", "intensity": 0.7}),
                repository=JsonRepository(tmp_path / "s.json"), canon="Ти — Лілі.", model="m",
                clock=lambda: now, mood_enabled=False)
    outbox = Outbox(tmp_path / "outbox.jsonl", mirror_user=mirror_user)
    app = create_app(core, token=TOKEN, env="dev", version="2.5.0", outbox=outbox)
    (tmp_path / "schedule.toml").write_text(schedule, encoding="utf-8")
    settings = {
        "bridge": True, "inbox_path": tmp_path / "inbox.jsonl", "outbox_path": tmp_path / "outbox.jsonl",
        "scheduler": bool(schedule), "schedule_path": tmp_path / "schedule.toml",
        "schedule_state_path": tmp_path / "schedule.state", "thoughts": False, "idle_nudge": False,
        "thoughts_quiet_hours": None, "thoughts_spoken_ratio": 0.0, "sched_show_to_outbox": False,
    }
    cfg = dataclasses.replace(load_config(load_env=False), **{**settings, **cfg_kw})
    return BrainHost(app.state.service, cfg, clock=lambda: now, rng=rng), app, core, now


def _outbox(tmp_path) -> list[tuple[str, str]]:
    return [(r["kind"], r["text"]) for r in fifo.read_since(tmp_path / "outbox.jsonl", 0)]


def _drain(sub) -> list:
    out = []
    while not sub.empty():
        out.append(sub.get_nowait())
    return out


# --- the inbox → turn → outbox round-trip ------------------------------------------------------------------

def test_a_telegram_line_becomes_a_turn_and_her_reply_goes_out(tmp_path):
    host, app, core, now = _setup(tmp_path)
    fifo.append(tmp_path / "inbox.jsonl", "привіт з телеграму", source="telegram")
    sub = app.state.service.bus.subscribe()
    host.inbox_tick(now)
    assert _outbox(tmp_path) == [("lili", "Я тут, привіт!")]  # her reply only — the line is never echoed
    events = _drain(sub)
    assert [(d["role"], d["text"], d["source"]) for e, d in events if e == "message"] == [
        ("user", "привіт з телеграму", "telegram"), ("lili", "Я тут, привіт!", "telegram")]
    assert "turn" in [e for e, _ in events]
    assert app.state.service.last_input == now  # a Telegram line is real input (the idle anchor)


def test_the_inbox_pointer_advances_server_side_each_line_runs_once(tmp_path):
    host, app, core, now = _setup(tmp_path)
    fifo.append(tmp_path / "inbox.jsonl", "раз", source="telegram")
    fifo.append(tmp_path / "inbox.jsonl", "два", source="telegram")
    host.inbox_tick(now)
    host.inbox_tick(now)  # caught up — nothing runs again
    assert core.totals.turns == 2 and len(_outbox(tmp_path)) == 2
    assert fifo.load_pointer(tmp_path / "inbox.pos") == 2


def test_the_inbox_waits_while_a_turn_holds_the_lock(tmp_path):
    host, app, core, now = _setup(tmp_path)
    fifo.append(tmp_path / "inbox.jsonl", "привіт", source="telegram")
    app.state.service.lock.acquire()
    try:
        host.inbox_tick(now)
    finally:
        app.state.service.lock.release()
    assert core.totals.turns == 0 and fifo.load_pointer(tmp_path / "inbox.pos") == 0  # not even read
    host.inbox_tick(now)
    assert core.totals.turns == 1


def test_a_dictated_line_is_tagged_voice(tmp_path):
    host, app, core, now = _setup(tmp_path)
    fifo.append(tmp_path / "inbox.jsonl", "сказав голосом", source="voice")
    sub = app.state.service.bus.subscribe()
    host.inbox_tick(now)
    assert {d["source"] for e, d in _drain(sub) if e == "message"} == {"voice"}


# --- client turns, spoken turns and shown thoughts reach the outbox ----------------------------------------

def test_a_client_turn_mirrors_your_line_then_her_reply(tmp_path):
    host, app, core, now = _setup(tmp_path)
    TestClient(app).post("/v1/turn", json={"text": "привіт з клієнта"}, headers=ME)
    assert _outbox(tmp_path) == [("user", "привіт з клієнта"), ("lili", "Я тут, привіт!")]


def test_with_only_the_voicer_a_client_turn_mirrors_her_reply_alone(tmp_path):
    host, app, core, now = _setup(tmp_path, mirror_user=False)
    TestClient(app).post("/v1/turn", json={"text": "привіт"}, headers=ME)
    assert _outbox(tmp_path) == [("lili", "Я тут, привіт!")]


def test_a_spoken_thought_goes_out_too(tmp_path):
    host, app, core, now = _setup(tmp_path, schedule='[[schedule]]\ndirective = "think"\nevery = "10m"\nshow = true\n',
                                  rng=lambda a, b: 0, thoughts_spoken_ratio=1.0)
    host.scheduler_tick(now)
    assert _outbox(tmp_path) == [("lili", "Я тут, привіт!")]


def test_a_shown_thought_goes_out_only_when_asked(tmp_path):
    every = '[[schedule]]\ndirective = "think"\nevery = "10m"\nshow = true\n'
    host, app, core, now = _setup(tmp_path, schedule=every)
    host.scheduler_tick(now)
    assert _outbox(tmp_path) == []  # LUMI_SCHED_SHOW_TO_OUTBOX off
    other = tmp_path / "on"
    other.mkdir()
    host2, app2, core2, now2 = _setup(other, schedule=every, sched_show_to_outbox=True)
    host2.scheduler_tick(now2)
    assert _outbox(other) == [("lili", THOUGHT)]


def test_the_server_wiring_helpers(tmp_path):
    cfg = dataclasses.replace(load_config(load_env=False), outbox_path=tmp_path / "o.jsonl")
    assert outbox_of(dataclasses.replace(cfg, bridge=False, voice=False)) is None
    assert outbox_of(dataclasses.replace(cfg, bridge=True)) == Outbox(tmp_path / "o.jsonl", mirror_user=True)
    assert outbox_of(dataclasses.replace(cfg, bridge=False, voice=True)).mirror_user is False
    assert telegram_sink_of(dataclasses.replace(cfg, bridge=True, image=False)) is None
    assert callable(telegram_sink_of(dataclasses.replace(cfg, bridge=True, image=True)))


# --- ids + the replay -------------------------------------------------------------------------------------

def test_every_event_has_an_id_and_only_conversation_is_kept():
    bus = EventBus(replay_max=3)
    bus.publish("state", {"state": {}})
    bus.publish("message", {"text": "1"})
    bus.publish("turn", {"emotion": "calm"})
    bus.publish("thought", {"text": "2"})
    fresh = bus.subscribe(replay=True)
    replayed = _drain(fresh)
    assert [d["text"] for _, d in replayed] == ["1", "2"] and all(d["replay"] is True for _, d in replayed)
    assert [i.eid for i in replayed] == [f"{bus.boot}-2", f"{bus.boot}-4"]


def test_a_resuming_listener_gets_only_what_it_missed():
    bus = EventBus()
    for n in range(4):
        bus.publish("message", {"text": str(n)})
    assert [d["text"] for _, d in _drain(bus.subscribe(replay=True, last_event_id=f"{bus.boot}-2"))] == ["2", "3"]
    assert [d["text"] for _, d in _drain(bus.subscribe(replay=True, last_event_id="0ther-9"))] == ["0", "1", "2", "3"]
    assert _drain(bus.subscribe()) == []  # no replay asked — the v2.4 behaviour


def test_the_replay_is_bounded():
    bus = EventBus(replay_max=2)
    for n in range(5):
        bus.publish("message", {"text": str(n)})
    assert [d["text"] for _, d in _drain(bus.subscribe(replay=True))] == ["3", "4"]


def _listen(client, headers):
    bus = client.app.state.service.bus
    got: dict = {}
    t = threading.Thread(target=lambda: got.update(res=client.get("/v1/events", headers=headers)))
    t.start()
    deadline = time.monotonic() + 5
    while bus.listeners == 0:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    bus.close()
    t.join(5)
    return got["res"].text


def test_a_fresh_listener_gets_what_happened_meanwhile(tmp_path):
    host, app, core, now = _setup(tmp_path)
    client = TestClient(app)
    fifo.append(tmp_path / "inbox.jsonl", "привіт з телеграму", source="telegram")
    host.inbox_tick(now)  # happened while no client was connected
    raw = _listen(client, AUTH)
    frames = [f for f in raw.strip().split("\n\n") if f.startswith("event:")]
    assert frames[0].startswith("event: state\n") and "id:" not in frames[0]
    replayed = [f for f in frames if f.startswith("event: message")]
    assert len(replayed) == 2 and all('"replay": true' in f and "\nid: " in f for f in replayed)
    assert "привіт з телеграму" in replayed[0] and "Я тут, привіт!" in replayed[1]


def test_last_event_id_resumes_without_repeats(tmp_path):
    host, app, core, now = _setup(tmp_path)
    client = TestClient(app)
    fifo.append(tmp_path / "inbox.jsonl", "привіт", source="telegram")
    host.inbox_tick(now)
    bus = app.state.service.bus
    last = f"{bus.boot}-{bus._seq}"
    raw = _listen(client, {**AUTH, "Last-Event-ID": last})
    assert "event: message" not in raw  # nothing missed — nothing replayed

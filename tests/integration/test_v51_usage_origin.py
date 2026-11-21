"""v5.1 (LUMI-340): each entry point says what started the work — a window turn, a CLI or unprefixed client, a
Telegram line, a scheduled row, an idle think, a typed directive. Over the mock model and a fake clock; the records
are read back from the instance's ``cache-log.jsonl`` and its messages from the store. No network, no paid API.
"""

import dataclasses
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from core import cache_log
from core.agent import Core
from core.config import load_config
from core.llm import MockLLMClient
from server.app import create_app
from server.brain import BrainHost
from state.local_store import JsonRepository
from tui.remote import CLI_PREFIX, TUI_PREFIX, RemoteCore

TOKEN = "origin-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
THOUGHT = "море шумить у тиші"


def _world(tmp_path, schedule: str = "", **cfg_kw):
    clock = lambda: datetime(2026, 11, 21, 12, 0, tzinfo=UTC)  # noqa: E731
    llm = MockLLMClient(f"{THOUGHT}\nЕМОЦІЯ: tender",
                        states={"reply": "Я тут", "emotion": "tender", "intensity": 0.6})
    core = Core(llm=llm, repository=JsonRepository(tmp_path / "s.json"), canon="Ти — Лілі.", model="m",
                clock=clock, mood_enabled=False, cache_monitor=True, cache_log_path=tmp_path / "cache-log.jsonl")
    app = create_app(core, token=TOKEN, env="dev", version="5.1.0")
    (tmp_path / "schedule.toml").write_text(schedule, encoding="utf-8")
    settings = {
        "scheduler": bool(schedule), "schedule_path": tmp_path / "schedule.toml",
        "schedule_state_path": tmp_path / "schedule.state", "sched_day_cap": 24, "thoughts_quiet_hours": None,
        "thoughts_spoken_ratio": 0.0, "thoughts": False, "idle_nudge": False, "quiet_hours": None,
    }
    cfg = dataclasses.replace(load_config(load_env=False), **{**settings, **cfg_kw})
    host = BrainHost(app.state.service, cfg, clock=clock, rng=lambda a, b: 0)
    return core, app, host, clock


def _records(tmp_path) -> list:
    return cache_log.load_events(tmp_path / "cache-log.jsonl")


def _messages(core) -> list:
    return core._repo.load_messages(core._repo.list_sessions("owner")[-1].id)


@pytest.mark.parametrize(("client_id", "channel"), [
    ("desktop-1a2b3c4d", "window"), (TUI_PREFIX + "abc", "tui"), (CLI_PREFIX + "abc", "cli"), (None, "api"),
])
def test_a_server_turn_records_the_owners_turn_and_its_channel(tmp_path, client_id, channel):
    core, app, _, _ = _world(tmp_path)
    headers = {**AUTH, **({"X-Lumi-Client": client_id} if client_id else {})}
    res = TestClient(app).post("/v1/turn", json={"text": "привіт"}, headers=headers)
    assert res.status_code == 200
    replies = [r for r in _records(tmp_path) if r.kind in ("reply", "tool")]
    assert replies and {(r.origin, r.channel) for r in replies} == {("turn", channel)}
    assert [m.channel for m in _messages(core)] == [channel, channel]


def test_a_typed_directive_records_its_name(tmp_path):
    _, app, _, _ = _world(tmp_path)
    res = TestClient(app).post("/v1/directive", json={"line": "%think про море"},
                               headers={**AUTH, "X-Lumi-Client": "desktop-1"})
    assert res.status_code == 200 and res.json()["is_directive"] is True
    thinks = [r for r in _records(tmp_path) if r.kind == "think"]
    assert thinks and {(r.origin, r.channel) for r in thinks} == {("directive:think", "window")}


def test_a_telegram_line_is_the_owners_turn_through_telegram(tmp_path):
    core, _, host, _ = _world(tmp_path)
    host._inbox_turn({"id": 1, "text": "привіт з телеграму"})
    replies = [r for r in _records(tmp_path) if r.kind == "reply"]
    assert replies and {(r.origin, r.channel) for r in replies} == {("turn", "telegram")}
    assert [m.channel for m in _messages(core)] == ["telegram", "telegram"]


def test_a_scheduled_row_and_the_spoken_turn_it_graduates_to_record_the_row(tmp_path):
    row = '[[schedule]]\nname = "muse-aloud"\ndirective = "think"\nevery = "10m"\nshow = true\n'
    core, _, host, clock = _world(tmp_path, row, thoughts_spoken_ratio=1.0)
    host.scheduler_tick(clock())
    records = _records(tmp_path)
    assert {r.origin for r in records if r.kind in ("think", "reply")} == {"schedule:muse-aloud"}
    assert any(r.kind == "reply" for r in records)  # the graduated spoken turn
    assert [m.channel for m in _messages(core)] == [None, None]  # her own turn: no channel


def test_an_unnamed_row_records_its_directive(tmp_path):
    core, _, host, clock = _world(tmp_path, '[[schedule]]\ndirective = "think"\nevery = "10m"\nshow = false\n')
    host.scheduler_tick(clock())
    assert {r.origin for r in _records(tmp_path) if r.kind == "think"} == {"schedule:think"}


def test_the_idle_think_records_idle(tmp_path):
    core, _, host, clock = _world(tmp_path, thoughts=True)
    core._thoughts_interval_s = 0
    from datetime import timedelta
    host.idle_tick(clock() + timedelta(minutes=5))
    thinks = [r for r in _records(tmp_path) if r.kind == "think"]
    assert thinks and {r.origin for r in thinks} == {"idle"}


def test_the_clients_name_themselves_with_their_prefix():
    tui = RemoteCore("http://127.0.0.1:1", "t", client=object())
    cli = RemoteCore("http://127.0.0.1:1", "t", client=object(), client_prefix=CLI_PREFIX)
    assert tui.client_id.startswith("tui-") and cli.client_id.startswith("cli-")
    assert len(tui.client_id) == len("tui-") + 12

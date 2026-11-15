"""v3.1 LUMI-282 — the DoD as one test: a room turn beside his leaves no trace on his side and never waits for him.

His turn runs on a slow mock model (held by an event) and holds the server's lock; meanwhile the room observes lines
and gets a turn answered. Then his turn finishes. The room's work never touches his state: ``last_emotion``,
``last_stats``, ``totals``, the idle clock, the outbox (his Telegram / voicer bus), the event stream (his desktop and
TUI), his session and his repository (no room line, no vector). Fake models only — no network, no paid call.
"""

from __future__ import annotations

import copy
import queue
import threading

from core.embedder import MockEmbedder
from core.llm import MockLLMClient
from server.app import Outbox
from tests._agora_world import OWNER_AUTH, ROOM_AUTH, FakeRoomModel, agora_client, make_core

ROOM_LINES = {"channel": "agora", "lines": [
    {"id": "$a", "speaker": "Ада", "text": "Хто бачив туман над містом?", "ts": "2026-11-16T08:01:12+02:00"},
    {"id": "$b", "speaker": "Ich", "text": "Я бачив, як він сідав на дахи.", "ts": "2026-11-16T08:02:40+02:00"},
]}
ROOM_TURN = {"channel": "agora", "speaker": "Бруно", "register": "voice", "turn_id": "$trig",
             "text": "Лілі, а ти що думаєш про туман?"}


def _drain(sub: queue.Queue) -> list[tuple[str, dict]]:
    out = []
    while True:
        try:
            out.append(sub.get_nowait())
        except queue.Empty:
            return out


def test_a_room_turn_beside_his_leaves_his_state_alone(tmp_path):
    release, inside = threading.Event(), threading.Event()

    def his_model(system, messages, model):
        inside.set()
        assert release.wait(10)
        return {"reply": "Я тут, з тобою.", "emotion": "tender", "intensity": 0.7}

    core = make_core(tmp_path, llm=MockLLMClient(states=his_model))
    core._embedder = MockEmbedder()  # his recall on, so a room line in his vectors would show
    outbox = tmp_path / "outbox.jsonl"
    room = FakeRoomModel()
    client = agora_client(tmp_path, core=core, room_model=room, outbox=Outbox(outbox, mirror_user=True))
    service = client.app.state.service
    his_session = service.session
    sub = service.bus.subscribe()

    his: list = []
    runner = threading.Thread(target=lambda: his.append(
        client.post("/v1/turn", json={"text": "Привіт, Лілі"}, headers=OWNER_AUTH)))
    runner.start()
    assert inside.wait(5)  # his turn is inside the model and holds the server's lock
    assert service.lock.locked()
    idle_anchor = service.last_input
    _drain(sub)

    # the room is heard and answered while his turn still runs — it never waits for him
    assert client.post("/v1/observe", json=ROOM_LINES, headers=ROOM_AUTH).json() == {"stored": 2, "known": 0}
    res = client.post("/v1/turn", json=ROOM_TURN, headers=ROOM_AUTH)
    assert res.status_code == 200 and res.json()["outcome"] == "reply"
    assert service.lock.locked() and runner.is_alive()  # his turn is still running, undisturbed
    assert _drain(sub) == []  # nothing of the room on his event stream (his desktop, his TUI)
    assert service.last_input == idle_anchor  # the room never resets his idle clock
    outbox_mid = outbox.read_text(encoding="utf-8") if outbox.exists() else ""
    assert "туман" not in outbox_mid and "Туман" not in outbox_mid  # nothing of the room toward his Telegram

    release.set()
    runner.join(10)
    assert his[0].status_code == 200 and his[0].json()["reply"] == "Я тут, з тобою."

    # his state is his turn's alone
    assert core.last_emotion.emotion.value == "tender" and core.totals.turns == 1
    assert core.last_stats.model == "m"
    events = _drain(sub)
    assert {e for e, _ in events} <= {"turn", "message", "state"}
    assert all("туман" not in str(data).lower() for _, data in events)
    outbox_text = outbox.read_text(encoding="utf-8")
    assert "Я тут, з тобою." in outbox_text and "туман" not in outbox_text.lower()
    # his session and his memory hold his words only
    assert service.session is his_session
    texts = [m.text for m in core._repo.load_messages(his_session.id)]
    assert texts == ["Привіт, Лілі", "Я тут, з тобою."]
    his_files = [f for f in tmp_path.rglob("*") if f.is_file() and not f.name.startswith("spaces.sqlite")]
    assert his_files and all("туман" not in f.read_bytes().decode("utf-8", "ignore").lower() for f in his_files)
    assert room.calls == 1  # the room answered once, on its own model


def test_the_room_never_moves_his_totals_or_his_last_state(tmp_path):
    core = make_core(tmp_path)
    client = agora_client(tmp_path, core=core)
    client.post("/v1/turn", json={"text": "привіт"}, headers=OWNER_AUTH)
    before = (core.last_emotion, core.last_stats, copy.deepcopy(core.totals),
              client.get("/v1/state", headers=OWNER_AUTH).json())
    client.post("/v1/observe", json=ROOM_LINES, headers=ROOM_AUTH)
    client.post("/v1/turn", json=ROOM_TURN, headers=ROOM_AUTH)
    after = (core.last_emotion, core.last_stats, core.totals, client.get("/v1/state", headers=OWNER_AUTH).json())
    assert after == before


def test_his_new_session_never_closes_the_rooms(tmp_path):
    client = agora_client(tmp_path)
    space = client.app.state.space
    client.post("/v1/observe", json=ROOM_LINES, headers=ROOM_AUTH)
    assert client.post("/v1/session/new", headers=OWNER_AUTH).status_code == 200
    assert [m.session_id for m in space.store.window("agora", 10)] == ["agora-2026-11-16"] * 2
    client.post("/v1/observe", json={"channel": "agora", "lines": [
        {"id": "$c", "speaker": "Мія", "text": "А я спала.", "ts": "2026-11-16T08:20:00+02:00"}]}, headers=ROOM_AUTH)
    assert {m.session_id for m in space.store.window("agora", 10)} == {"agora-2026-11-16"}  # the same room session


def test_the_stop_closes_the_room_beside_his_session(tmp_path):
    from server.__main__ import close_session, close_space

    client = agora_client(tmp_path)
    space = client.app.state.space
    client.post("/v1/observe", json=ROOM_LINES, headers=ROOM_AUTH)
    assert close_session(client.app.state.service, wait_s=1) is True
    close_space(space)
    from state.space_store import SqliteSpaceStore

    assert len(SqliteSpaceStore(tmp_path / "spaces.sqlite").window("agora", 10)) == 2  # committed, reopened whole

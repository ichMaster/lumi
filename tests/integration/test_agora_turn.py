"""v3.3 LUMI-292 — a room day end to end on fake models: observe, a reply, a pass, a retract — and what she keeps.

After the retract, neither the room's own window, her recall in the room, nor his «Агора сьогодні» and recall hold the
retracted reply; the pass left nothing; the room turn's own overhead (everything but the model) stays well under a
second. No network, no paid call."""

from __future__ import annotations

import time

from core.embedder import MockEmbedder
from core.llm import MockLLMClient
from tests._agora_world import FakeRoomModel, agora_client, make_core, make_space

ROOM_AUTH = {"Authorization": "Bearer room-token", "X-Lumi-Client": "agora"}
SECRET_REPLY = "Туман — це мій улюблений сірий шарф для міста."


def test_a_room_day_observe_reply_pass_retract(tmp_path):
    emb = MockEmbedder()
    core = make_core(tmp_path, llm=MockLLMClient(states={"reply": "так", "emotion": "calm", "intensity": 0.5}))
    core._embedder, core._recall_enabled, core._rag_enabled, core._rag_floor = emb, True, True, 0.1
    room = RecordingRoom({"reply": SECRET_REPLY, "emotion": "playful", "intensity": 0.6})
    space = make_space(tmp_path, room)
    space._embedder = emb
    core.attach_space(space)
    client = agora_client(tmp_path, core=core, space=space)

    lines = [{"id": f"$l{i}", "speaker": ("Ада", "Бруно")[i % 2], "text": f"туман над містом {i}",
              "ts": f"2026-11-16T08:0{i}:00+02:00"} for i in range(4)]
    assert client.post("/v1/observe", json={"channel": "agora", "lines": lines}, headers=ROOM_AUTH).json() == {
        "stored": 4, "known": 0}
    t0 = time.monotonic()
    reply = client.post("/v1/turn", json={"channel": "agora", "speaker": "Ада", "turn_id": "$r",
                                          "text": "Лілі, що для тебе туман?"}, headers=ROOM_AUTH).json()
    overhead = time.monotonic() - t0
    assert reply["outcome"] == "reply" and reply["reply"] == SECRET_REPLY
    assert overhead < 1.0  # everything but the model's own seconds

    room.state = {"reply": ""}
    passed = client.post("/v1/turn", json={"channel": "agora", "speaker": "Бруно", "turn_id": "$p",
                                           "text": "А ти, Лілі?"}, headers=ROOM_AUTH).json()
    assert passed["outcome"] == "pass" and passed["reply"] == ""
    assert space.store.get("agora", "$p", "lili") is None  # a pass left nothing

    assert core._space_today() and SECRET_REPLY in core._space_today()  # before: «Агора сьогодні» has it
    assert client.post("/v1/turn/%24r/retract", headers=ROOM_AUTH).json() == {"retracted": True}
    assert space.store.get("agora", "$r", "lili") is None
    assert SECRET_REPLY not in (core._space_today() or "")  # his «Агора сьогодні» no longer holds it
    assert SECRET_REPLY not in (core._recall_block("мій улюблений сірий шарф") or "")  # nor his recall
    room.state = {"reply": "Так.", "emotion": "calm", "intensity": 0.4}
    client.post("/v1/turn", json={"channel": "agora", "speaker": "Ада", "turn_id": "$next",
                                  "text": "сірий шарф?"}, headers=ROOM_AUTH)
    last_prompt = "\n".join(m["content"] for m in room_messages(room))
    assert SECRET_REPLY not in last_prompt  # nor the room's own window or recall


class RecordingRoom(FakeRoomModel):
    """The room's fake model, keeping the last prompt it was given (system + messages)."""

    last_messages: list = []

    def reply_structured(self, system, messages, model, cache_prefix=None, **kw):
        self.last_messages = [{"content": system}, *messages]
        return super().reply_structured(system, messages, model, cache_prefix, **kw)


def room_messages(room: FakeRoomModel) -> list:
    return room.last_messages

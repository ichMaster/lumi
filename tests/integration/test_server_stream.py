"""The v2.3 streamed turn over the API (LUMI-216) — SSE events, turn_id idempotency, disconnects.

FastAPI's TestClient against a mock-model core with the v1.4 streaming seam on; no sockets, no paid API.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from core.agent import Core
from core.llm import LLMError, MockLLMClient
from server.app import create_app
from state.local_store import JsonRepository

TOKEN = "stream-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _client(tmp_path, llm, *, stream=True):
    core = Core(llm=llm, repository=JsonRepository(tmp_path / "s.json"), canon="Ти — Лілі.",
                model="claude-haiku-4-5-20251001", stream=stream)
    return TestClient(create_app(core, token=TOKEN, env="dev", version="2.3.0")), core


def _events(raw: str) -> list[tuple[str, dict]]:
    out = []
    for frame in raw.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in frame.splitlines() if ": " in line)
        out.append((fields["event"], json.loads(fields["data"])))
    return out


def _stream(client, text, turn_id):
    res = client.post("/v1/turn/stream", json={"text": text, "turn_id": turn_id}, headers=AUTH)
    assert res.status_code == 200
    return _events(res.text)


def test_the_deltas_assemble_to_the_reply_and_the_emotion_arrives_only_in_done(tmp_path):
    llm = MockLLMClient(states={"reply": "Привіт, як ти сьогодні?", "emotion": "joy", "intensity": 0.7},
                        stream_chunk=4)
    client, _ = _client(tmp_path, llm)
    events = _stream(client, "привіт", "t-1")
    deltas = [d["text"] for e, d in events if e == "delta"]
    assert len(deltas) > 1  # genuinely incremental
    assert all(set(d) == {"text"} for e, d in events if e == "delta")  # no emotion mid-stream
    kind, done = events[-1]
    assert kind == "done" and "".join(deltas) == done["reply"] == "Привіт, як ти сьогодні?"
    assert done["emotion"] == "joy" and done["intensity"] == 0.7


def test_think_streams_apart_from_the_prose(tmp_path):
    llm = MockLLMClient(states={"reply": "<think>зважую слова</think>Готово, друже", "emotion": "calm",
                                "intensity": 0.5})
    client, _ = _client(tmp_path, llm)
    events = _stream(client, "привіт", "t-2")
    assert "зважую слова" in "".join(d["text"] for e, d in events if e == "think")
    assert "".join(d["text"] for e, d in events if e == "delta") == "Готово, друже"


def test_a_malformed_reply_is_validated_on_completion(tmp_path):
    llm = MockLLMClient(states={"reply": "ок", "emotion": "ecstatic", "intensity": 9})
    client, _ = _client(tmp_path, llm)
    done = _stream(client, "?", "t-3")[-1][1]
    assert done["emotion"] == "calm" and done["intensity"] == 1.0


def test_stream_then_blocking_with_the_same_id_is_one_turn(tmp_path):
    client, core = _client(tmp_path, MockLLMClient(states={"reply": "так", "emotion": "calm", "intensity": 0.5}))
    streamed = _stream(client, "привіт", "t-4")[-1][1]
    again = client.post("/v1/turn", json={"text": "привіт", "turn_id": "t-4"}, headers=AUTH).json()
    assert again["reply"] == streamed["reply"] and core.totals.turns == 1
    restream = _stream(client, "привіт", "t-4")  # a known id skips straight to the outcome
    assert [e for e, _ in restream] == ["done"] and core.totals.turns == 1


def test_a_client_that_disconnects_mid_turn_doesnt_stop_it(tmp_path):
    def slow(system, messages, model):
        time.sleep(0.4)
        return {"reply": "пізніше, але повністю", "emotion": "tender", "intensity": 0.6}

    client, core = _client(tmp_path, MockLLMClient(states=slow))
    with client.stream("POST", "/v1/turn/stream", json={"text": "привіт", "turn_id": "t-5"}, headers=AUTH):
        pass  # gone before a single event
    recovered = client.post("/v1/turn", json={"text": "привіт", "turn_id": "t-5"}, headers=AUTH).json()
    assert recovered["reply"] == "пізніше, але повністю" and core.totals.turns == 1
    nxt = client.post("/v1/turn", json={"text": "ще", "turn_id": "t-6"}, headers=AUTH)
    assert nxt.status_code == 200  # the lock was released by the runner, not by the vanished client


def test_a_core_that_doesnt_stream_still_answers_with_done(tmp_path):
    client, _ = _client(tmp_path, MockLLMClient(states={"reply": "добре", "emotion": "calm", "intensity": 0.5}),
                        stream=False)
    events = _stream(client, "привіт", "t-7")
    assert [e for e, _ in events] == ["done"] and events[0][1]["reply"] == "добре"


def test_a_model_failure_is_an_error_event_and_frees_the_lock(tmp_path):
    def boom(system, messages, model):
        raise LLMError("model down")

    client, _ = _client(tmp_path, MockLLMClient(boom))
    events = _stream(client, "?", "t-8")
    assert events == [("error", {"detail": "model unavailable: model down"})]
    assert client.get("/v1/state", headers=AUTH).json()["stream_enabled"] is True
    client.app.state.service.core._llm = MockLLMClient(states={"reply": "ок", "emotion": "calm", "intensity": 0.5})
    assert _stream(client, "ще раз", "t-9")[-1][0] == "done"


def test_a_different_turn_while_one_runs_is_409(tmp_path):
    client, _ = _client(tmp_path, MockLLMClient("x"))
    service = client.app.state.service
    service.lock.acquire()
    try:
        res = client.post("/v1/turn/stream", json={"text": "привіт", "turn_id": "t-10"}, headers=AUTH)
        assert res.status_code == 409
    finally:
        service.lock.release()


@pytest.mark.parametrize("n", [40])
def test_old_finished_turns_are_forgotten_but_never_a_running_one(tmp_path, n):
    client, _ = _client(tmp_path, MockLLMClient(states={"reply": "так", "emotion": "calm", "intensity": 0.5}))
    for i in range(n):
        client.post("/v1/turn", json={"text": f"{i}", "turn_id": f"k-{i}"}, headers=AUTH)
    kept = client.app.state.service._turns
    assert len(kept) <= 32 and "k-0" not in kept and f"k-{n - 1}" in kept

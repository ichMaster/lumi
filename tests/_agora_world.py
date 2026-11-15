"""Shared fixtures for the v3.1 Agora server tests: a core on a mock model, the space on a fake room model."""

from __future__ import annotations

import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from core.agent import Core
from core.clock import fixed_clock
from core.embedder import MockEmbedder
from core.llm import MockLLMClient, ResponseStats
from core.space import AgoraSpace
from server.app import create_app
from state.local_store import JsonRepository
from state.space_store import SqliteSpaceStore

OWNER = "owner-token"
ROOM = "room-token"
OWNER_AUTH = {"Authorization": f"Bearer {OWNER}"}
ROOM_AUTH = {"Authorization": f"Bearer {ROOM}", "X-Lumi-Client": "agora"}
NOW = datetime(2026, 11, 16, 8, 10, tzinfo=ZoneInfo("Europe/Kyiv"))


class FakeRoomModel:
    """The room's client: a canned state (by default the contract's example reply), never the network."""

    def __init__(self, state=None, *, error: Exception | None = None, hold: threading.Event | None = None):
        self.state = state or {"reply": "Туман — це коли місто на хвилину забуває свої кути. Мені подобається "
                                         "така м'якість.", "emotion": "thoughtful", "intensity": 0.5}
        self.error = error
        self.hold = hold
        self.entered = threading.Event()
        self.calls = 0
        self.last_stats: ResponseStats | None = None

    def reply_structured(self, system, messages, model, cache_prefix=None, **_):
        self.calls += 1
        self.entered.set()
        if self.hold is not None:
            self.hold.wait(10)
        if self.error is not None:
            raise self.error
        self.last_stats = ResponseStats(model=model, latency_ms=1840, input_tokens=6420, output_tokens=38)
        return dict(self.state)


def make_core(tmp_path, *, llm=None) -> Core:
    return Core(llm=llm or MockLLMClient(states={"reply": "так", "emotion": "calm", "intensity": 0.5}),
                repository=JsonRepository(tmp_path / "store.json"), canon="C", model="m", clock=fixed_clock(NOW))


def make_space(tmp_path, room_model=None, **kw) -> AgoraSpace:
    return AgoraSpace(SqliteSpaceStore(tmp_path / "spaces.sqlite"), room_model or FakeRoomModel(),
                      model="gemini-2.5-flash", canon="C", clock=fixed_clock(NOW), embedder=MockEmbedder(), **kw)


def agora_client(tmp_path, *, core=None, space=None, room_model=None, **kw) -> TestClient:
    core = core or make_core(tmp_path)
    space = space or make_space(tmp_path, room_model)
    app = create_app(core, token=OWNER, env="dev", version="3.1.0", space=space, room_token=ROOM, **kw)
    return TestClient(app)

"""Contract (v3.2): the isolation invariant restated for spaces — **never a space reads anyone's relationship memory**.

Every layer of his memory (facts, the identity core, session summaries, day and week digests, his messages and their
vectors) and her thoughts carry a sentinel string in a ``Core`` beside the room. The room's prompt, built the way the
server wires it (the core's canon, mood, clock and embedder shared), holds none of it, holds the room rule, and never
his real name (``agora.private_names`` → «Ich»). ARCHITECTURE §Identity (the space-memory row) and §Spaces.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from core.agent import Core
from core.clock import fixed_clock
from core.embedder import MockEmbedder
from core.llm import MockLLMClient
from core.repository import (
    DaySummary,
    LongTermFact,
    ShortSummary,
    Thought,
    VectorRecord,
    WeekSummary,
    make_message,
)
from core.space import AgoraSpace, ObservedLine
from state.local_store import JsonRepository
from state.space_store import SqliteSpaceStore

SENTINEL = "SENTINEL-ВІТАЛІЙ-7f3a"
NOW = datetime(2026, 11, 16, 9, 0, tzinfo=ZoneInfo("Europe/Kyiv"))


class RoomModel:
    def __init__(self):
        self.prompts: list[tuple[str, list]] = []
        self.last_stats = None

    def reply_structured(self, system, messages, model, cache_prefix=None, **_):
        self.prompts.append((system, messages))
        return {"reply": "Доброго ранку, Агоро.", "emotion": "calm", "intensity": 0.4}


def _his_memory_full_of_sentinels(tmp_path) -> Core:
    repo = JsonRepository(tmp_path / "store.json")
    emb = MockEmbedder()
    core = Core(llm=MockLLMClient(), repository=repo, canon="Ти — Лілі.", model="m", clock=fixed_clock(NOW),
                embedder=emb, recall_enabled=True)
    s = repo.create_session("owner")
    repo.append_message(make_message(s.id, "owner", "user", f"{SENTINEL} його слова про туман"))
    repo.add_summary(ShortSummary("owner", s.id, f"{SENTINEL} summary", f"{SENTINEL} gist", "2026-11-15T20:00:00+02:00"))
    repo.set_day_summary(DaySummary("owner", "2026-11-15", f"{SENTINEL} day", 1, "2026-11-15T23:00:00+02:00"))
    repo.set_week_summary(WeekSummary("owner", "2026-11-09", f"{SENTINEL} week", 1, "2026-11-15T23:00:00+02:00"))
    repo.add_fact(LongTermFact("owner", f"{SENTINEL} core fact", "", 0.9, "2026-11-15T20:00:00+02:00", core=True))
    repo.add_fact(LongTermFact("owner", f"{SENTINEL} fact", "", 0.9, "2026-11-15T20:00:00+02:00"))
    repo.add_thought(Thought("2026-11-16T08:00", "think", f"{SENTINEL} thought", "calm", ("mood",), "owner"))
    [vec] = emb.embed([f"{SENTINEL} туман над містом"])
    repo.add_vector(VectorRecord("owner", "m1", tuple(vec), f"{SENTINEL} туман над містом",
                                 "2026-11-15T20:00:00+02:00", "user"))
    return core


def test_the_rooms_prompt_holds_nothing_of_his(tmp_path):
    core = _his_memory_full_of_sentinels(tmp_path)
    room = RoomModel()
    space = AgoraSpace(SqliteSpaceStore(tmp_path / "spaces.sqlite"), room, model="gemini-2.5-flash",
                       canon=core._canon, clock=core.clock, embedder=core._embedder, mood=lambda: core.mood,
                       private_names=["Віталій", "Vitalii"])
    space.observe([ObservedLine("$a", "Ада", "Хто бачив туман над містом?", "2026-11-16T08:01:00+02:00"),
                   ObservedLine("$b", "Бруно", "Віталій казав, що туман м'який.", "2026-11-16T08:02:00+02:00")])
    space.turn("Ich", "Лілі, а ти що думаєш про туман?", "$trig")
    [(system, messages)] = room.prompts
    prompt = system + "\n" + "\n".join(m["content"] for m in messages)
    assert SENTINEL not in prompt and "SENTINEL" not in prompt
    assert "«Ich» — це той, з ким ти говориш і наодинці" in system  # the room rule
    assert "Віталій" not in prompt and "Vitalii" not in prompt  # his real name never (R3)
    assert "Ich казав, що туман м'який." in prompt  # a room line naming him reads «Ich»


def test_the_rooms_recall_never_reaches_his_vectors(tmp_path):
    core = _his_memory_full_of_sentinels(tmp_path)
    space = AgoraSpace(SqliteSpaceStore(tmp_path / "spaces.sqlite"), RoomModel(), model="m", canon="C",
                       clock=core.clock, embedder=core._embedder)
    space.observe([ObservedLine("$x", "Ада", "туман над містом учора", "2026-11-14T08:00:00+02:00")])
    trigger = space._new_lines([ObservedLine("$t", "Бруно", "туман над містом", "2026-11-16T08:05:00+02:00")])[0]
    recalled = space._room_recall(trigger, set())
    assert recalled and "Ада: туман над містом учора" in recalled and SENTINEL not in recalled

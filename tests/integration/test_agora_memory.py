"""v3.2 LUMI-288 — the DoD's measurable half: his prompt's growth is bounded on a busy room day, his memory is
unmoved by any number of room lines, and his real name never reaches the room's prompt (end to end, through the
server). Fake models only — no network, no paid call."""

from __future__ import annotations

import copy
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from core.agent import Core
from core.clock import fixed_clock
from core.embedder import MockEmbedder
from core.llm import MockLLMClient
from core.repository import DaySummary, LongTermFact, make_message
from core.space import AgoraSpace, ObservedLine, SpaceDigest, SpaceGist, cut_words
from server.app import create_app
from state.local_store import JsonRepository
from state.space_store import SqliteSpaceStore
from tests._agora_world import OWNER, OWNER_AUTH, ROOM, ROOM_AUTH, FakeRoomModel

KYIV = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 11, 16, 21, 0, tzinfo=KYIV)
WORDS = "туман дах місто кава море книжка вітер ліхтар дощ сад ".split()


def _core(tmp_path, emb) -> Core:
    return Core(llm=MockLLMClient(states={"reply": "так", "emotion": "calm", "intensity": 0.5}),
                repository=JsonRepository(tmp_path / "store.json"), canon="C", model="m", clock=fixed_clock(NOW),
                embedder=emb, recall_enabled=True, rag_enabled=True, rag_floor=0.1, day_days=7, week_days=30)


def _space(tmp_path, emb, model=None, **kw) -> AgoraSpace:
    return AgoraSpace(SqliteSpaceStore(tmp_path / "spaces.sqlite"), model or FakeRoomModel(), model="m", canon="C",
                      clock=fixed_clock(NOW), embedder=emb, today_lines=20, today_digest_words=150, gist_words=40,
                      **kw)


def _line(i: int, day: str = "2026-11-16") -> ObservedLine:
    text = " ".join(WORDS[(i + j) % len(WORDS)] for j in range(8))  # ~60 characters, like a room line
    return ObservedLine(f"${day}-{i}", ("Ада", "Бруно", "Мія", "Кіра", "Ich")[i % 5], text,
                        f"{day}T{8 + i // 60:02d}:{i % 60:02d}:00+02:00")


def test_a_busy_room_day_grows_his_prompt_within_the_bound(tmp_path):
    emb = MockEmbedder()
    plain = _core(tmp_path / "plain", emb)
    core = _core(tmp_path / "room", emb)
    for c in (plain, core):
        c._repo.set_day_summary(DaySummary("owner", "2026-11-15", "Говорили про море.", 1, "2026-11-15T23:00:00+02:00"))
    space = _space(tmp_path, emb)
    core.attach_space(space)
    space.observe([_line(i) for i in range(140)])  # matrix-agora's R2: ~140 room lines a day
    digest = cut_words("слово " * 400, 150)
    space.store.set_digest(SpaceDigest("agora", "agora-2026-11-16", digest, 120))
    gists = [SpaceGist("agora", f"2026-11-{d:02d}", cut_words("подія " * 80, 40)) for d in range(9, 16)]
    for g in gists:
        space.store.add_gist(g)

    without, _ = plain._system_prompt(plain.start_session())
    with_room, prefix = core._system_prompt(core.start_session())
    growth = len(with_room) - len(without)
    lines = space.store.session_lines("agora", "agora-2026-11-16")[-20:]
    from core.space import OWNER_LABEL, ROOM_DATA_NOTE

    bound = (sum(len(OWNER_LABEL if m.speaker == "Ich" else m.speaker) + 2 + len(m.text) + 1 for m in lines)  # 20 lines
             + len(digest) + len("в Агорі: ") + len(gists[-1].text) + 2  # the digest + the one day digest line's gist
             + len(ROOM_DATA_NOTE) + 300)  # the note (v3.3.1), the section headers and the quoted block's marks
    assert 0 < growth <= bound
    assert growth < 8000  # ≈ 2 K tokens of Cyrillic, the owner's sizing (2026-11-15)
    assert prefix.find("# Агора сьогодні") == -1 and with_room.index("# Агора сьогодні") > len(prefix)
    block = core._recall_block(" ".join(WORDS[:8]))
    assert block is None or len(block) <= core._rag_max_chars + 2  # the shared recall budget holds


def test_no_room_that_day_no_block(tmp_path):
    emb = MockEmbedder()
    core = _core(tmp_path, emb)
    space = _space(tmp_path, emb)
    core.attach_space(space)
    space.observe([_line(i, day="2026-11-15") for i in range(30)])  # yesterday only
    assert "# Агора сьогодні" not in core._system_prompt(core.start_session())[0]


def _his_snapshot(core: Core, tmp_path) -> tuple:
    repo = core._repo
    return (copy.deepcopy(repo.get_closeness("owner")), [f.fact for f in repo.facts("owner")],
            [(s.id, [m.text for m in repo.load_messages(s.id)]) for s in repo.list_sessions("owner")],
            (tmp_path / "store.json").read_text(encoding="utf-8") if (tmp_path / "store.json").exists() else "")


def test_any_number_of_room_lines_leaves_his_memory_unmoved(tmp_path):
    emb = MockEmbedder()
    core = _core(tmp_path, emb)
    core._repo.add_fact(LongTermFact("owner", "Любить туман над містом.", "", 0.9, "2026-11-01T10:00:00+02:00",
                                     core=True))
    s = core._repo.create_session("owner")
    core._repo.append_message(make_message(s.id, "owner", "user", "привіт"))
    core.reply("як ти?", core.start_session())  # his closeness record exists, a turn of his has run
    space = _space(tmp_path, emb)
    client = TestClient(create_app(core, token=OWNER, env="dev", version="3.2.0", space=space, room_token=ROOM))
    before = _his_snapshot(core, tmp_path)
    for batch in range(5):  # 500 room lines — «Ich» among the speakers
        lines = [{"id": f"$x{batch}-{i}", "speaker": ("Ich", "Ада")[i % 2], "text": f"кімната {batch}-{i}",
                  "ts": f"2026-11-16T{9 + batch:02d}:{i % 60:02d}:{i // 60:02d}+02:00"} for i in range(100)]
        assert client.post("/v1/observe", json={"channel": "agora", "lines": lines}, headers=ROOM_AUTH).status_code == 200
    for t in range(10):
        res = client.post("/v1/turn", json={"channel": "agora", "speaker": "Ich", "turn_id": f"$turn{t}",
                                            "text": f"Лілі, питання {t}"}, headers=ROOM_AUTH)
        assert res.status_code == 200
    assert _his_snapshot(core, tmp_path) == before  # closeness, facts, sessions, the store file — all his, unmoved
    assert "## Про Віталія\n- Любить туман над містом." in core._system_prompt(core.start_session())[0]


def test_his_real_name_never_reaches_the_rooms_prompt_through_the_server(tmp_path):
    room = FakeRoomModel()
    seen: list[str] = []
    real = room.reply_structured

    def recording(system, messages, model, cache_prefix=None, **kw):
        seen.append(system + "\n".join(m["content"] for m in messages))
        return real(system, messages, model)

    room.reply_structured = recording
    emb = MockEmbedder()
    space = _space(tmp_path, emb, room, private_names=["Віталій"])
    space.store.add_gist(SpaceGist("agora", "2026-11-15", "Віталія вітали в Агорі."))
    client = TestClient(create_app(_core(tmp_path, emb), token=OWNER, env="dev", version="3.2.0", space=space,
                                   room_token=ROOM))
    client.post("/v1/observe", json={"channel": "agora", "lines": [
        {"id": "$a", "speaker": "Ада", "text": "Віталій сьогодні в Агорі?", "ts": "2026-11-16T08:00:00+02:00"}]},
        headers=ROOM_AUTH)
    client.post("/v1/turn", json={"channel": "agora", "speaker": "Бруно", "turn_id": "$t",
                                  "text": "Лілі, а Віталію сподобається?"}, headers=ROOM_AUTH)
    assert seen and all("Вітал" not in prompt for prompt in seen)
    assert client.get("/v1/state", headers=OWNER_AUTH).status_code == 200

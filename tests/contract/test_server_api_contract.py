"""Contract: the server API v1 (LUMI-212) — the route set, the token rule, the payload shapes.

Every client (the TUI client mode, the CLI, the pywebview client in v2.8) depends on exactly this. A
change here is a contract change: ARCHITECTURE §Contracts and this test move together.
"""

import json

from fastapi.testclient import TestClient

from core.agent import Core
from core.emotion import Emotion
from core.llm import MockLLMClient
from server.app import create_app
from state.local_store import JsonRepository

TOKEN = "contract-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _client(tmp_path):
    core = Core(llm=MockLLMClient(states={"reply": "так", "emotion": "calm", "intensity": 0.5}),
                repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m")
    return TestClient(create_app(core, token=TOKEN, env="dev", version="2.2.0"))


def test_the_route_set_is_pinned(tmp_path):
    routes = {(m, r.path) for r in _client(tmp_path).app.routes if r.path.startswith("/v1/")
              for m in (getattr(r, "methods", None) or {"WS"})}
    assert routes == {
        ("GET", "/v1/health"), ("GET", "/v1/state"), ("POST", "/v1/turn"),
        ("POST", "/v1/command"), ("POST", "/v1/session/new"),
        ("POST", "/v1/turn/stream"),  # v2.3: the streamed turn (SSE)
        ("GET", "/v1/events"),  # v2.4: the push channel (SSE)
        ("POST", "/v1/directive"),  # v2.4: a typed %directive runs on the server
        ("GET", "/v1/files"), ("GET", "/v1/files/text"), ("GET", "/v1/files/raw"),  # v4.1: her sandbox, read-only
        ("GET", "/v1/images"), ("GET", "/v1/images/thumb"),  # v4.2: her pictures and their thumbnails, read-only
        ("GET", "/v1/journal"), ("GET", "/v1/journal/{date}"),  # v4.3: her journal, the owner's only
        ("GET", "/v1/thoughts"),  # v4.3: her thought stream through the surfacing filter
        ("GET", "/v1/today"), ("GET", "/v1/schedule"),  # v4.4: her day and her timetable
        ("POST", "/v1/files/upload"), ("POST", "/v1/files/rename"), ("POST", "/v1/files/trash"),  # v4.5: the owner's
        ("POST", "/v1/files/restore"), ("GET", "/v1/files/download"),  # actions on her sandbox
        ("GET", "/v1/voice/{voice_id}"), ("POST", "/v1/voice"),  # v4.6: her voice — a line's audio, the switch
        ("WS", "/v1/dictation"),  # v4.7: dictation — the owner's audio in, the words out
    }


def test_no_public_docs_or_schema(tmp_path):
    client = _client(tmp_path)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_only_health_is_open(tmp_path):
    client = _client(tmp_path)
    assert client.get("/v1/health").status_code == 200
    assert client.get("/v1/state").status_code == 401


def test_turn_carries_the_emotion_contract(tmp_path):
    res = _client(tmp_path).post("/v1/turn", json={"text": "?"}, headers=AUTH).json()
    assert set(res) == {"reply", "emotion", "intensity", "thinking", "intent", "style", "stats", "timing", "state"}
    # v2.6: the turn's stages on the server + server_ms (received → outcome) — a remote client's network hop
    assert set(res["timing"]) == {"pre_ms", "llm_ms", "post_ms", "total_ms", "server_ms"}
    assert res["timing"]["server_ms"] >= 0
    assert isinstance(res["reply"], str) and res["reply"]
    assert res["emotion"] in {e.value for e in Emotion}
    assert 0.0 <= res["intensity"] <= 1.0


def test_state_shape_is_pinned(tmp_path):
    state = _client(tmp_path).get("/v1/state", headers=AUTH).json()
    assert set(state) == {
        "env", "version", "session_id", "model", "provider", "profile", "thinking", "style",
        "last_emotion", "last_intent", "last_thinking", "last_ttft_ms", "last_stats", "totals",
        "mood", "theme", "think_show",
        "stream_enabled",  # v2.3
        "closeness",  # v2.4
        "features",  # v2.6: what the server can do — a remote client gates /image and /web on it
        "cycle",  # v4.4: today's hormonal phase {phase, day, length}, or None
    }
    assert set(state["closeness"]) == {"level", "name"}  # the level by name — never the raw value
    assert state["features"] == {"image": False, "web_lookup": False, "files": False,  # a bare core: all off
                                 "gallery": False,  # v4.2
                                 # v4.3: the views read what exists — the journal root always has a default (the
                                 # owner reads it), and the thought stream is served unless /thoughts is off
                                 "journal": True, "thoughts": True,
                                 # v4.4: her day is always served; the schedule only while a scheduler runs
                                 "today": True, "schedule": False,
                                 "actions": False,  # v4.5: no sandbox and no action log on a bare core
                                 "dictation": False}  # v4.7: no recognizer on a bare core
    assert set(state["totals"]) == {
        "turns", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "latency_ms",
    }


def test_command_shape_is_pinned(tmp_path):
    res = _client(tmp_path).post("/v1/command", json={"line": "/mood"}, headers=AUTH).json()
    assert set(res) == {"handled", "result", "state"}
    assert set(res["result"]) == {"text", "kind", "confirm", "turn"}



def _events(raw: str) -> list[tuple[str, dict]]:
    out = []
    for frame in raw.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines() if not line.startswith(":"))
        if "event" in lines:  # a ':' heartbeat comment carries no event
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_the_stream_event_set_and_done_shape(tmp_path):
    client = _client(tmp_path)
    res = client.post("/v1/turn/stream", json={"text": "?", "turn_id": "c-1"}, headers=AUTH)
    assert res.headers["content-type"].startswith("text/event-stream")
    events = _events(res.text)
    assert {e for e, _ in events} <= {"delta", "think", "done", "error"}
    assert events[-1][0] == "done" and [e for e, _ in events].count("done") == 1  # exactly one outcome
    done = events[-1][1]
    assert set(done) == {"reply", "emotion", "intensity", "thinking", "intent", "style", "stats", "timing", "state"}
    assert "server_ms" in done["timing"]  # v2.6: the streamed outcome carries it too


def test_a_known_turn_id_never_runs_twice(tmp_path):
    client = _client(tmp_path)
    core = client.app.state.service.core
    first = client.post("/v1/turn", json={"text": "привіт", "turn_id": "c-2"}, headers=AUTH).json()
    again = client.post("/v1/turn", json={"text": "привіт", "turn_id": "c-2"}, headers=AUTH).json()
    assert again["reply"] == first["reply"]
    assert core.totals.turns == 1  # the retry returned the stored result — no second turn


def test_the_stream_route_needs_the_token(tmp_path):
    assert _client(tmp_path).post("/v1/turn/stream", json={"text": "?"}).status_code == 401


# --- v2.4: the push channel -------------------------------------------------------------------------------

def _heard(client, action) -> list[tuple[str, dict]]:
    import threading
    import time

    bus = client.app.state.service.bus
    got: dict = {}
    t = threading.Thread(target=lambda: got.update(res=client.get("/v1/events", headers=AUTH)))
    t.start()
    deadline = time.monotonic() + 5
    while bus.listeners == 0:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    action()
    bus.close()  # ends the stream (the server's shutdown does the same)
    t.join(5)
    return _events(got["res"].text)


def test_the_push_event_set_and_shapes(tmp_path):
    client = _client(tmp_path)
    heard = _heard(client, lambda: client.post("/v1/turn", json={"text": "?"},
                                               headers={**AUTH, "X-Lumi-Client": "c-9"}))
    assert {e for e, _ in heard} <= {"state", "turn", "thought", "message"}
    assert heard[0] == ("state", {"origin": None, "state": heard[0][1]["state"]})  # the listener's start
    turn = next(d for e, d in heard if e == "turn")
    assert set(turn) == {"emotion", "intensity", "theme", "origin"}  # the face signal — nothing more
    assert turn["emotion"] in {e.value for e in Emotion} and 0.0 <= turn["intensity"] <= 1.0
    assert turn["origin"] == "c-9"  # the requester's X-Lumi-Client
    assert all(set(d) == {"origin", "state"} for e, d in heard if e == "state")


def test_no_face_assets_ever_cross_the_api(tmp_path):
    """The face is a client-side concern (local packs): a theme NAME may cross, never a file or a path.

    v4.2 narrowed the v2.4 rule to the face: her sandbox's pictures cross only through the owner's read routes
    (``/v1/files/raw``, ``/v1/images``, ``/v1/images/thumb``) and as sandbox paths on her ``message`` / the outcome."""
    client = _client(tmp_path)
    for route in client.app.routes:
        assert "face" not in route.path
        assert "image" not in route.path or route.path in {"/v1/images", "/v1/images/thumb"}
    heard = _heard(client, lambda: client.post("/v1/turn", json={"text": "?"}, headers=AUTH))
    raw = json.dumps(heard, ensure_ascii=False)
    for marker in (".png", ".jpg", ".webp", "faces/", "base64"):
        assert marker not in raw
    theme = next(d for e, d in heard if e == "turn")["theme"]
    assert theme is None or ("/" not in theme and "." not in theme)


def test_directive_shape_is_pinned(tmp_path):
    res = _client(tmp_path).post("/v1/directive", json={"line": "%think!"}, headers=AUTH).json()
    assert set(res) == {"is_directive", "mode", "thought", "saved_to", "state"}
    assert res["thought"] is None or set(res["thought"]) == {"kind", "text", "emotion", "when"}


# --- v2.5: the server's own acts ----------------------------------------------------------------------------

def test_a_client_request_waits_for_the_servers_own_act_not_409(tmp_path):
    import threading
    import time

    client = _client(tmp_path)
    service = client.app.state.service
    held = threading.Event()

    def act():
        with service.background():  # the brain host's lock for a scheduled act
            held.set()
            time.sleep(0.2)

    threading.Thread(target=act).start()
    assert held.wait(2)
    assert client.post("/v1/command", json={"line": "/mood"}, headers=AUTH).status_code == 200


def test_a_spoken_message_shape_is_pinned(tmp_path):
    import dataclasses

    from core.config import load_config
    from server.brain import BrainHost

    client = _client(tmp_path)
    service = client.app.state.service
    sub = service.bus.subscribe()
    host = BrainHost(service, dataclasses.replace(load_config(load_env=False), scheduler=False))
    with service.background():
        host._spoken_turn("(seed)", source="thought")
    events = []
    while not sub.empty():
        events.append(sub.get_nowait())
    message = next(d for e, d in events if e == "message")
    assert set(message) == {"role", "text", "source", "emotion", "intensity", "origin"}
    assert message["role"] == "lili" and message["source"] in {"client", "telegram", "voice", "thought", "nudge"}
    assert message["origin"] is None and message["emotion"] in {e.value for e in Emotion}


def test_conversation_events_carry_ids_and_replay_to_a_fresh_listener(tmp_path):
    client = _client(tmp_path)
    client.post("/v1/turn", json={"text": "?"}, headers={**AUTH, "X-Lumi-Client": "c-1"})
    heard_raw: dict = {}

    def listen():
        heard_raw["text"] = client.get("/v1/events", headers=AUTH).text

    import threading
    import time

    bus = client.app.state.service.bus
    t = threading.Thread(target=listen)
    t.start()
    deadline = time.monotonic() + 5
    while bus.listeners == 0:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    bus.close()
    t.join(5)
    frames = [f for f in heard_raw["text"].strip().split("\n\n") if f.startswith("event:")]
    messages = [f for f in frames if f.startswith("event: message")]
    assert len(messages) == 2  # the turn's two lines, replayed — what happened before this listener came
    for frame in messages:
        fields = dict(line.split(": ", 1) for line in frame.splitlines())
        assert fields["id"].startswith(f"{bus.boot}-")  # SSE ids: <boot>-<seq>
        data = json.loads(fields["data"])
        assert data["replay"] is True and data["source"] == "client" and data["origin"] == "c-1"


def test_the_agora_space_adds_its_routes_only(tmp_path):
    # v3.1: with the space on, the route set gains POST /v1/observe; v3.3: and POST /v1/turn/{turn_id}/retract;
    # v3.4: and her initiative's two (the agora space API v2). Off, it is the set above.
    from tests._agora_world import agora_client

    on = {(m, r.path) for r in agora_client(tmp_path).app.routes if r.path.startswith("/v1/")
          for m in (getattr(r, "methods", None) or {"WS"})}
    off = {(m, r.path) for r in _client(tmp_path / "off").app.routes if r.path.startswith("/v1/")
           for m in (getattr(r, "methods", None) or {"WS"})}
    assert on - off == {("POST", "/v1/observe"), ("POST", "/v1/turn/{turn_id}/retract"),
                        ("GET", "/v1/initiative"), ("POST", "/v1/initiative/{initiative_id}/posted")} and off <= on


def test_a_turn_without_a_channel_is_todays(tmp_path):
    # v3.1: the new fields default to his channel — the payload is the same set as before, nothing added.
    res = _client(tmp_path).post("/v1/turn", json={"text": "?", "channel": "owner"}, headers=AUTH).json()
    assert set(res) == {"reply", "emotion", "intensity", "thinking", "intent", "style", "stats", "timing", "state"}


# --- v4.1 LUMI-302: her sandbox for the window's views — read-only, owner-only, never the lock --------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40


def _sandbox(files_dir, user_id="owner"):
    root = files_dir / user_id
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "ideas.md").write_text("# Ідеї\n\n- дощ\n", encoding="utf-8")
    (root / "art").mkdir()
    (root / "art" / "p.png").write_bytes(PNG)
    (root / "art" / "x.svg").write_text("<svg><script>x</script></svg>", encoding="utf-8")
    return root


def _files_core(tmp_path, user_id="owner"):
    return Core(llm=MockLLMClient(states={"reply": "так", "emotion": "calm", "intensity": 0.5}),
                repository=JsonRepository(tmp_path / f"s-{user_id}.json"), canon="C", model="m",
                files_dir=tmp_path / "files", user_id=user_id)


def _files_client(tmp_path, user_id="owner"):
    return TestClient(create_app(_files_core(tmp_path, user_id), token=TOKEN, env="dev", version="4.1.0"))


def _fingerprint(root):
    import hashlib
    import os
    rows = []
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            p = os.path.join(dirpath, name)
            st = os.stat(p)
            rows.append((p, st.st_size, st.st_mtime_ns, hashlib.sha256(open(p, "rb").read()).hexdigest()))
    return sorted(rows)


def test_v41_the_three_reads_answer_with_their_shapes(tmp_path):
    _sandbox(tmp_path / "files")
    client = _files_client(tmp_path)
    folder = client.get("/v1/files", headers=AUTH)
    assert folder.status_code == 200 and folder.headers["cache-control"] == "no-store"
    body = folder.json()
    assert set(body) == {"path", "entries", "truncated"} and body["path"] == ""
    assert [e["name"] for e in body["entries"]] == ["art", "notes"]
    assert set(body["entries"][0]) == {"name", "kind", "type", "size", "count", "created", "modified"}
    text = client.get("/v1/files/text", params={"path": "notes/ideas.md"}, headers=AUTH).json()
    assert text == {"path": "notes/ideas.md", "text": "# Ідеї\n\n- дощ\n", "size": len("# Ідеї\n\n- дощ\n".encode()),
                    "cut": False, "type": "markdown"}
    raw = client.get("/v1/files/raw", params={"path": "art/p.png"}, headers=AUTH)
    assert raw.status_code == 200 and raw.content == PNG and raw.headers["content-type"] == "image/png"
    assert raw.headers["x-content-type-options"] == "nosniff"
    assert raw.headers["content-disposition"] == "inline"
    assert raw.headers["cache-control"] == "private, max-age=300"


def test_v41_features_say_whether_files_are_served(tmp_path):
    assert _files_client(tmp_path).get("/v1/state", headers=AUTH).json()["features"]["files"] is True
    assert _client(tmp_path).get("/v1/state", headers=AUTH).json()["features"]["files"] is False
    assert _client(tmp_path).get("/v1/files", headers=AUTH).status_code == 404  # no sandbox: not served


def test_v41_the_reads_are_the_owners_only(tmp_path):
    from tests._agora_world import OWNER_AUTH, ROOM_AUTH, agora_client
    _sandbox(tmp_path / "files")
    client = agora_client(tmp_path, core=_files_core(tmp_path))
    for path, params in (("/v1/files", {}), ("/v1/files/text", {"path": "notes/ideas.md"}),
                         ("/v1/files/raw", {"path": "art/p.png"})):
        assert client.get(path, params=params).status_code == 401
        assert client.get(path, params=params, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.get(path, params=params, headers=ROOM_AUTH).status_code == 403  # the room never reads her files
        assert client.get(path, params=params, headers=OWNER_AUTH).status_code == 200


def test_v41_refusals_are_mapped_and_name_no_absolute_path(tmp_path):
    _sandbox(tmp_path / "files")
    client = _files_client(tmp_path)
    cases = [("/v1/files", "../elsewhere", 400), ("/v1/files", "nope", 404), ("/v1/files/text", "art/p.png", 415),
             ("/v1/files/raw", "art/x.svg", 415), ("/v1/files/raw", "notes/ideas.md", 415),
             ("/v1/files/raw", "/etc/hosts", 400), ("/v1/files/text", "nope.md", 404)]
    for path, rel, status in cases:
        res = client.get(path, params={"path": rel}, headers=AUTH)
        assert res.status_code == status, (path, rel, res.status_code)
        assert str(tmp_path) not in res.text


def test_v41_a_picture_over_the_cap_is_413(tmp_path, monkeypatch):
    from core import browse
    _sandbox(tmp_path / "files")
    monkeypatch.setattr(browse, "BROWSE_MAX_PICTURE", 8)
    assert _files_client(tmp_path).get("/v1/files/raw", params={"path": "art/p.png"}, headers=AUTH).status_code == 413


def test_v41_a_read_never_waits_for_the_turn_lock(tmp_path):
    _sandbox(tmp_path / "files")
    client = _files_client(tmp_path)
    service = client.app.state.service
    assert service.lock.acquire(timeout=1)  # a turn that runs for minutes holds it
    try:
        assert client.get("/v1/files", headers=AUTH).status_code == 200
        assert client.get("/v1/files/text", params={"path": "notes/ideas.md"}, headers=AUTH).status_code == 200
    finally:
        service.lock.release()


def test_v41_each_user_reads_only_their_own_sandbox(tmp_path):
    _sandbox(tmp_path / "files", "owner")
    bob = _sandbox(tmp_path / "files", "bob")
    (bob / "notes" / "bob-only.md").write_text("bob", encoding="utf-8")
    owner = _files_client(tmp_path, "owner")
    names = [e["name"] for e in owner.get("/v1/files", params={"path": "notes"}, headers=AUTH).json()["entries"]]
    assert "bob-only.md" not in names
    assert owner.get("/v1/files/text", params={"path": "../bob/notes/bob-only.md"}, headers=AUTH).status_code == 400
    assert owner.get("/v1/files", params={"path": "../bob"}, headers=AUTH).status_code == 400


def test_v41_the_reads_leave_the_sandbox_byte_identical(tmp_path):
    root = _sandbox(tmp_path / "files")
    before = _fingerprint(root)
    client = _files_client(tmp_path)
    for path, rel in (("/v1/files", ""), ("/v1/files", "notes"), ("/v1/files/text", "notes/ideas.md"),
                      ("/v1/files/raw", "art/p.png"), ("/v1/files/raw", "art/x.svg"), ("/v1/files", "../x")):
        client.get(path, params={"path": rel}, headers=AUTH)
    assert _fingerprint(root) == before


def test_review_v41_2_a_nul_byte_in_the_path_is_400(tmp_path):
    _sandbox(tmp_path / "files")
    client = _files_client(tmp_path)
    for path in ("/v1/files", "/v1/files/text", "/v1/files/raw"):
        res = client.get(path, params={"path": "notes/a\x00b"}, headers=AUTH)
        assert res.status_code == 400 and "malformed" in res.json()["detail"], path


# --- v4.2 LUMI-306: her pictures for the Gallery and in the chat -------------------------------------------------

def _real_png(size=(400, 300)) -> bytes:
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, (180, 40, 40)).save(buf, "PNG")
    return buf.getvalue()


def _gallery_sandbox(files_dir, user_id="owner"):
    import os
    root = _sandbox(files_dir, user_id)
    (root / "art" / "p.png").write_bytes(_real_png((400, 300)))
    (root / "art" / "q.png").write_bytes(_real_png((800, 200)))
    os.utime(root / "art" / "p.png", ns=(1_700_000_100 * 10**9,) * 2)
    os.utime(root / "art" / "q.png", ns=(1_700_000_200 * 10**9,) * 2)
    return root


def _gallery_client(tmp_path, user_id="owner"):
    return TestClient(create_app(_files_core(tmp_path, user_id), token=TOKEN, env="dev", version="4.2.0",
                                 thumbs_dir=tmp_path / "cache" / "thumbs"))


def test_v42_the_gallery_routes_answer_with_their_shapes(tmp_path):
    _gallery_sandbox(tmp_path / "files")
    client = _gallery_client(tmp_path)
    res = client.get("/v1/images", headers=AUTH)
    assert res.status_code == 200 and res.headers["cache-control"] == "no-store"
    body = res.json()
    assert set(body) == {"items", "next", "total", "folders", "truncated"}
    assert [i["path"] for i in body["items"]] == ["art/q.png", "art/p.png"]  # newest first
    assert set(body["items"][0]) == {"path", "folder", "size", "modified", "width", "height"}
    assert (body["items"][0]["width"], body["items"][0]["height"]) == (800, 200)
    assert body["folders"] == [{"folder": "art", "count": 2}] and body["total"] == 2 and body["next"] is None
    page = client.get("/v1/images", params={"limit": 1}, headers=AUTH).json()
    rest = client.get("/v1/images", params={"limit": 1, "before": page["next"]}, headers=AUTH).json()
    assert [i["path"] for i in page["items"] + rest["items"]] == ["art/q.png", "art/p.png"] and rest["next"] is None
    thumb = client.get("/v1/images/thumb", params={"path": "art/q.png"}, headers=AUTH)
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    assert thumb.content[:3] == b"\xff\xd8\xff"
    assert thumb.headers["x-content-type-options"] == "nosniff"
    assert thumb.headers["content-disposition"] == "inline"
    assert thumb.headers["cache-control"] == "private, max-age=300"
    assert len(list((tmp_path / "cache" / "thumbs" / "owner").glob("*.jpg"))) == 1  # cached per user


def test_v42_features_say_whether_the_gallery_is_served(tmp_path):
    assert _gallery_client(tmp_path).get("/v1/state", headers=AUTH).json()["features"]["gallery"] is True
    no_cache = _files_client(tmp_path)  # the sandbox but no thumbnail cache
    assert no_cache.get("/v1/state", headers=AUTH).json()["features"]["gallery"] is False
    _gallery_sandbox(tmp_path / "files")
    assert no_cache.get("/v1/images/thumb", params={"path": "art/p.png"}, headers=AUTH).status_code == 404
    assert _client(tmp_path).get("/v1/images", headers=AUTH).status_code == 404  # no sandbox: not served


def test_v42_the_gallery_is_the_owners_only(tmp_path):
    from tests._agora_world import OWNER_AUTH, ROOM_AUTH, agora_client
    _gallery_sandbox(tmp_path / "files")
    client = agora_client(tmp_path, core=_files_core(tmp_path), thumbs_dir=tmp_path / "cache" / "thumbs")
    for path, params in (("/v1/images", {}), ("/v1/images/thumb", {"path": "art/p.png"})):
        assert client.get(path, params=params).status_code == 401
        assert client.get(path, params=params, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.get(path, params=params, headers=ROOM_AUTH).status_code == 403  # the room never sees them
        assert client.get(path, params=params, headers=OWNER_AUTH).status_code == 200


def test_v42_refusals_are_mapped_and_name_no_absolute_path(tmp_path, monkeypatch):
    from core import browse
    _gallery_sandbox(tmp_path / "files")
    client = _gallery_client(tmp_path)
    cases = [("/v1/images", {"before": "%%%"}, 400), ("/v1/images", {"limit": 0}, 400),
             ("/v1/images", {"limit": 101}, 400), ("/v1/images/thumb", {"path": "art/x.svg"}, 415),
             ("/v1/images/thumb", {"path": "notes/ideas.md"}, 415), ("/v1/images/thumb", {"path": "art/no.png"}, 404),
             ("/v1/images/thumb", {"path": "../elsewhere.png"}, 400), ("/v1/images/thumb", {"path": "/etc/hosts"}, 400),
             ("/v1/images/thumb", {"path": "art/a\x00b.png"}, 400)]
    for path, params, status in cases:
        res = client.get(path, params=params, headers=AUTH)
        assert res.status_code == status, (path, params, res.status_code)
        assert str(tmp_path) not in res.text
    monkeypatch.setattr(browse, "BROWSE_MAX_PICTURE", 8)
    assert client.get("/v1/images/thumb", params={"path": "art/p.png"}, headers=AUTH).status_code == 413


def test_v42_the_gallery_never_waits_for_the_turn_lock(tmp_path):
    _gallery_sandbox(tmp_path / "files")
    client = _gallery_client(tmp_path)
    service = client.app.state.service
    assert service.lock.acquire(timeout=1)  # a turn that runs for minutes holds it
    try:
        assert client.get("/v1/images", headers=AUTH).status_code == 200
        assert client.get("/v1/images/thumb", params={"path": "art/p.png"}, headers=AUTH).status_code == 200
    finally:
        service.lock.release()


def test_v42_each_user_sees_only_their_own_pictures_and_thumbnails(tmp_path):
    _gallery_sandbox(tmp_path / "files", "owner")
    bob = _gallery_sandbox(tmp_path / "files", "bob")
    (bob / "art" / "bob-only.png").write_bytes(_real_png((20, 20)))
    owner = _gallery_client(tmp_path, "owner")
    paths = [i["path"] for i in owner.get("/v1/images", headers=AUTH).json()["items"]]
    assert "art/bob-only.png" not in paths
    assert owner.get("/v1/images/thumb", params={"path": "../bob/art/bob-only.png"}, headers=AUTH).status_code == 400
    owner.get("/v1/images/thumb", params={"path": "art/p.png"}, headers=AUTH)
    assert (tmp_path / "cache" / "thumbs" / "owner").is_dir()
    assert not (tmp_path / "cache" / "thumbs" / "bob").exists()


def test_v42_the_gallery_leaves_the_sandbox_byte_identical(tmp_path):
    root = _gallery_sandbox(tmp_path / "files")
    before = _fingerprint(root)
    client = _gallery_client(tmp_path)
    for path, params in (("/v1/images", {}), ("/v1/images", {"folder": "art", "limit": 1}),
                         ("/v1/images/thumb", {"path": "art/p.png"}), ("/v1/images/thumb", {"path": "art/p.png"}),
                         ("/v1/images/thumb", {"path": "art/x.svg"}), ("/v1/images/thumb", {"path": "../x.png"})):
        client.get(path, params=params, headers=AUTH)
    assert _fingerprint(root) == before
    cache = tmp_path / "cache" / "thumbs" / "owner"
    assert cache.is_dir() and root not in cache.parents and cache not in root.parents


# --- v4.2: the turn's pictures on the outcome and her message — the face event unchanged ---

def _picture_core(tmp_path, tool_script, *, sink=None):
    def gen(prompt, *, size=768):
        return _real_png((64, 64))

    return Core(llm=MockLLMClient(states={"reply": "ось", "emotion": "tender", "intensity": 0.6},
                                  tool_script=tool_script),
                repository=JsonRepository(tmp_path / "s-pic.json"), canon="C", model="m",
                files_dir=tmp_path / "files", user_id="owner", mood_enabled=False, closeness_enabled=False,
                thoughts_enabled=False, image_enabled=True, image_gen=gen, image_show="path",
                telegram_sink=sink, tool_max_steps=5)


def test_v42_a_turn_that_makes_a_picture_carries_it_on_the_outcome_and_her_message(tmp_path):
    core = _picture_core(tmp_path, [("generate_image", {"prompt": "дах під дощем", "filename": "rooftop.png"})])
    client = TestClient(create_app(core, token=TOKEN, env="dev", version="4.2.0"))
    holder: dict = {}
    heard = _heard(client, lambda: holder.update(res=client.post("/v1/turn", json={"text": "намалюй"}, headers=AUTH)))
    assert holder["res"].json()["pictures"] == ["art/rooftop.png"]
    hers = [d for e, d in heard if e == "message" and d["role"] == "lili"]
    assert hers[-1]["pictures"] == ["art/rooftop.png"]
    assert all("pictures" not in d for e, d in heard if e == "message" and d["role"] == "user")
    turn = next(d for e, d in heard if e == "turn")
    assert set(turn) == {"emotion", "intensity", "theme", "origin"}  # the face event: never a path


def test_v42_the_streamed_outcome_carries_the_pictures_too(tmp_path):
    core = _picture_core(tmp_path, [("generate_image", {"prompt": "кіт", "filename": "cat.png"})])
    client = TestClient(create_app(core, token=TOKEN, env="dev", version="4.2.0"))
    res = client.post("/v1/turn/stream", json={"text": "?", "turn_id": "p-1"}, headers=AUTH)
    assert _events(res.text)[-1] == ("done", _events(res.text)[-1][1])
    assert _events(res.text)[-1][1]["pictures"] == ["art/cat.png"]


def test_v42_a_sent_picture_counts_and_still_reaches_the_sink(tmp_path):
    root = _gallery_sandbox(tmp_path / "files")
    sent: list = []
    core = _picture_core(tmp_path, [("send_image", {"path": "art/p.png", "caption": "глянь"})],
                         sink=lambda path, caption: sent.append((path, caption)))
    client = TestClient(create_app(core, token=TOKEN, env="dev", version="4.2.0"))
    assert client.post("/v1/turn", json={"text": "надішли"}, headers=AUTH).json()["pictures"] == ["art/p.png"]
    assert sent == [(str((root / "art" / "p.png").resolve()), "глянь")]  # Telegram as before


def test_v42_nothing_made_or_sent_means_no_pictures_field(tmp_path):
    _gallery_sandbox(tmp_path / "files")

    def failing(prompt, *, size=768):
        from core.imagegen import ImageGenError
        raise ImageGenError("down")

    for script, sink in (([("send_image", {"path": "art/p.png"})], None),  # the bridge is off: nothing sent
                         ([("send_image", {"path": "art/nope.png"})], lambda p, c: None),  # an error
                         ([], None)):  # no tool at all
        core = _picture_core(tmp_path, script, sink=sink)
        client = TestClient(create_app(core, token=TOKEN, env="dev", version="4.2.0"))
        assert "pictures" not in client.post("/v1/turn", json={"text": "?"}, headers=AUTH).json()
    core = _picture_core(tmp_path, [("generate_image", {"prompt": "x"})])
    core._image_gen = failing  # the image model fails: no file, no picture
    client = TestClient(create_app(core, token=TOKEN, env="dev", version="4.2.0"))
    assert "pictures" not in client.post("/v1/turn", json={"text": "?"}, headers=AUTH).json()


def test_v42_the_servers_own_turns_push_her_pictures(tmp_path):
    import dataclasses

    from core.config import load_config
    from server.brain import BrainHost

    core = _picture_core(tmp_path, [("generate_image", {"prompt": "сон", "filename": "dream.png"})])
    client = TestClient(create_app(core, token=TOKEN, env="dev", version="4.2.0"))
    service = client.app.state.service
    sub = service.bus.subscribe()
    host = BrainHost(service, dataclasses.replace(load_config(load_env=False), scheduler=False))
    with service.background():
        host._spoken_turn("(seed)", source="thought")
    events = []
    while not sub.empty():
        events.append(sub.get_nowait())
    message = next(d for e, d in events if e == "message")
    assert message["pictures"] == ["art/dream.png"] and message["source"] == "thought"
    core._llm = MockLLMClient(states={"reply": "так", "emotion": "calm", "intensity": 0.5},
                              tool_script=[("generate_image", {"prompt": "чай", "filename": "tea.png"})])
    host._inbox_turn({"id": 1, "text": "намалюй чай"})  # a Telegram line becomes a turn
    events = []
    while not sub.empty():
        events.append(sub.get_nowait())
    hers = [d for e, d in events if e == "message" and d["role"] == "lili"]
    assert hers[-1]["pictures"] == ["art/tea.png"] and hers[-1]["source"] == "telegram"


# --- v4.3 LUMI-311: her journal (the owner's only) and her thought stream for the window ---------------------------

DAY = ("# 2026-11-16\n\n> **Настрій:** Тихо.\n"
       "> **Біоритми:** фізичний +0.52 (rising) · емоційний +1.00 (high) · інтелектуальний -0.31 (falling)\n"
       "> **Прогноз:** Повільний день.\n\n## 00:30\n\nПерший запис.\n\n## 15:16\n\nДругий.\n")
OLD_DAY = "# 17 червня\n\nДень без штампа.\n\n### Нотатка\n\nТекст.\n"


def _journal(journal_dir, user_id="owner"):
    root = journal_dir / user_id
    root.mkdir(parents=True)
    (root / "2026-11-16.md").write_text(DAY, encoding="utf-8")
    (root / "2026-06-17.md").write_text(OLD_DAY, encoding="utf-8")
    return root


def _thought(when, kind="think", text="думка", user_id="owner", spoken=False):
    from core.repository import Thought
    return Thought(when=when, kind=kind, text=text, emotion="calm", seeds=("mood",), user_id=user_id, spoken=spoken)


def _views_core(tmp_path, user_id="owner", *, thoughts_show="hidden"):
    repo = JsonRepository(tmp_path / "store.json")
    if not (tmp_path / "store.json").exists():
        for i, (kind, user, spoken) in enumerate([("think", "owner", False), ("wonder", "ada", False),
                                                  ("wonder", "owner", True), ("learn", "owner", False),
                                                  ("think", "ada", True), ("think", "owner", False)]):
            repo.add_thought(_thought(f"2026-11-1{6 + i // 3}T10:0{i}", kind, f"{user} {i}", user, spoken))
    return Core(llm=MockLLMClient(states={"reply": "так", "emotion": "calm", "intensity": 0.5}), repository=repo,
                canon="C", model="m", user_id=user_id, journal_dir=tmp_path / "journal", thoughts_show=thoughts_show)


def _views_client(tmp_path, user_id="owner", **kw):
    return TestClient(create_app(_views_core(tmp_path, user_id, **kw), token=TOKEN, env="dev", version="4.3.0"))


def test_v43_the_journal_routes_answer_with_their_shapes(tmp_path):
    _journal(tmp_path / "journal")
    client = _views_client(tmp_path)
    days = client.get("/v1/journal", headers=AUTH)
    assert days.status_code == 200 and days.headers["cache-control"] == "no-store"
    assert days.json() == {"truncated": False, "days": [
        {"date": "2026-11-16", "sections": 2, "size": len(DAY.encode()), "stamp": True},
        {"date": "2026-06-17", "sections": 1, "size": len(OLD_DAY.encode()), "stamp": False}]}
    day = client.get("/v1/journal/2026-11-16", headers=AUTH)
    assert day.status_code == 200 and day.headers["cache-control"] == "no-store"
    body = day.json()
    assert set(body) == {"date", "stamp", "sections", "markdown"} and body["markdown"] == DAY
    assert body["stamp"]["mood"] == "Тихо." and body["stamp"]["forecast"] == "Повільний день."
    assert body["stamp"]["biorhythms"][1] == {"name": "емоційний", "value": 1.0, "label": "high"}
    assert body["sections"] == [{"time": "00:30", "text": "Перший запис."}, {"time": "15:16", "text": "Другий."}]
    old = client.get("/v1/journal/2026-06-17", headers=AUTH).json()
    assert old["stamp"] is None and old["sections"][0]["time"] is None and "### Нотатка" in old["sections"][0]["text"]


def test_v43_the_thoughts_route_answers_with_its_shape(tmp_path):
    client = _views_client(tmp_path)
    res = client.get("/v1/thoughts", headers=AUTH)
    assert res.status_code == 200 and res.headers["cache-control"] == "no-store"
    body = res.json()
    assert set(body) == {"items", "next", "stats", "days", "kinds"}
    assert [i["text"] for i in body["items"]] == ["owner 5", "owner 3", "owner 2", "owner 0"]
    assert set(body["items"][0]) == {"when", "kind", "text", "emotion", "spoken"}
    assert body["stats"] == {"total": 4, "per_day": 2.0, "spoken_share": 0.25}
    assert body["days"] == {"2026-11-17": 2, "2026-11-16": 2} and body["next"] is None
    assert body["kinds"] == ["think", "learn", "wonder"]
    page = client.get("/v1/thoughts", params={"limit": 3}, headers=AUTH).json()
    rest = client.get("/v1/thoughts", params={"limit": 3, "before": page["next"]}, headers=AUTH).json()
    assert [i["text"] for i in page["items"] + rest["items"]] == ["owner 5", "owner 3", "owner 2", "owner 0"]
    assert rest["next"] is None
    several = client.get("/v1/thoughts", params=[("kind", "wonder"), ("kind", "learn")], headers=AUTH).json()
    assert [i["text"] for i in several["items"]] == ["owner 3", "owner 2"]
    spoken = client.get("/v1/thoughts", params={"spoken": "true"}, headers=AUTH).json()
    assert [i["text"] for i in spoken["items"]] == ["owner 2"]
    silent = client.get("/v1/thoughts", params={"spoken": "false", "date": "2026-11-16"}, headers=AUTH).json()
    assert [i["text"] for i in silent["items"]] == ["owner 0"]


def test_v43_features_say_whether_the_journal_and_thoughts_are_served(tmp_path):
    features = _views_client(tmp_path).get("/v1/state", headers=AUTH).json()["features"]
    assert features["journal"] is True and features["thoughts"] is True
    ada = _views_client(tmp_path, "ada").get("/v1/state", headers=AUTH).json()["features"]
    assert ada["journal"] is False and ada["thoughts"] is True  # the journal is the owner's; her thoughts are filtered
    off = _views_client(tmp_path, thoughts_show="off")
    assert off.get("/v1/state", headers=AUTH).json()["features"]["thoughts"] is False
    assert off.get("/v1/thoughts", headers=AUTH).status_code == 404  # the owner turned the thought view off
    assert _views_client(tmp_path).get("/v1/journal", headers=AUTH).json() == {"days": [], "truncated": False}


def test_v43_a_second_users_server_reads_no_journal(tmp_path):
    _journal(tmp_path / "journal", "owner")
    _journal(tmp_path / "journal", "ada")  # even her own folder
    ada = _views_client(tmp_path, "ada")
    for path in ("/v1/journal", "/v1/journal/2026-11-16", "/v1/journal/2026-06-17"):
        res = ada.get(path, headers=AUTH)
        assert res.status_code == 403 and res.json()["detail"] == "the journal is the owner's", path
        assert "Тихо" not in res.text


def test_v43_the_reads_are_the_owners_token_only(tmp_path):
    from tests._agora_world import OWNER_AUTH, ROOM_AUTH, agora_client
    _journal(tmp_path / "journal")
    client = agora_client(tmp_path, core=_views_core(tmp_path))
    for path in ("/v1/journal", "/v1/journal/2026-11-16", "/v1/thoughts"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.get(path, headers=ROOM_AUTH).status_code == 403  # the room never reads her journal or thoughts
        assert client.get(path, headers=OWNER_AUTH).status_code == 200


def test_v43_a_thought_sparked_with_another_user_never_shows(tmp_path):
    owner = _views_client(tmp_path)
    texts = []
    cursor = None
    while True:
        page = owner.get("/v1/thoughts", params={"limit": 1, **({"before": cursor} if cursor else {})},
                         headers=AUTH).json()
        texts += [i["text"] for i in page["items"]]
        cursor = page["next"]
        if cursor is None:
            break
    assert texts == ["owner 5", "owner 3", "owner 2", "owner 0"]
    for kind in ("think", "wonder"):
        assert all("ada" not in i["text"] for i in owner.get("/v1/thoughts", params={"kind": kind},
                                                              headers=AUTH).json()["items"])
    ada = _views_client(tmp_path, "ada").get("/v1/thoughts", headers=AUTH).json()
    assert [i["text"] for i in ada["items"]] == ["ada 4", "ada 1"]


def test_v43_refusals_are_mapped_and_name_no_absolute_path(tmp_path):
    _journal(tmp_path / "journal")
    client = _views_client(tmp_path)
    cases = [("/v1/journal/2026-11-17", {}, 404), ("/v1/journal/2026-02-30", {}, 400),
             ("/v1/journal/16.11.2026", {}, 400), ("/v1/journal/..", {}, 404),
             ("/v1/thoughts", {"before": "%%%"}, 400), ("/v1/thoughts", {"limit": 0}, 400),
             ("/v1/thoughts", {"limit": 101}, 400), ("/v1/thoughts", {"spoken": "maybe"}, 400),
             ("/v1/thoughts", {"date": "2026-13-01"}, 400)]
    for path, params, status in cases:
        res = client.get(path, params=params, headers=AUTH)
        assert res.status_code == status, (path, params, res.status_code)
        assert str(tmp_path) not in res.text


def test_v43_the_reads_never_wait_for_the_turn_lock(tmp_path):
    _journal(tmp_path / "journal")
    client = _views_client(tmp_path)
    service = client.app.state.service
    assert service.lock.acquire(timeout=1)  # a turn that runs for minutes holds it
    try:
        for path in ("/v1/journal", "/v1/journal/2026-11-16", "/v1/thoughts"):
            assert client.get(path, headers=AUTH).status_code == 200, path
    finally:
        service.lock.release()


def test_v43_the_reads_leave_the_journal_and_the_store_byte_identical(tmp_path):
    root = _journal(tmp_path / "journal")
    client = _views_client(tmp_path)
    before = (_fingerprint(root), (tmp_path / "store.json").read_bytes())
    for path, params in (("/v1/journal", {}), ("/v1/journal/2026-11-16", {}), ("/v1/journal/2026-06-17", {}),
                         ("/v1/journal/2026-01-01", {}), ("/v1/thoughts", {}), ("/v1/thoughts", {"kind": "think"}),
                         ("/v1/thoughts", {"date": "2026-11-16", "spoken": "false", "limit": 1})):
        client.get(path, params=params, headers=AUTH)
    assert (_fingerprint(root), (tmp_path / "store.json").read_bytes()) == before


# --- v4.4 LUMI-316: her day, her timetable, and a turn's tools ----------------------------------------------------

NATAL = "Сонце 15° Риб\nНародження: 14.03.2001, 03:10, Київ"
READING = "Тиха ніч.\n\nРЕЗОЛЮЦІЯ: Зосереджена, без поспіху.\nТЕМА: quiet-collapse"
SCHEDULE_YAML = """schedule:
  - name: muse-silent
    seeds: core/think_seeds.md
    idle: "15m"
    enabled: true
  - name: morning-brief
    directive: brief
    at: "08:00"
    enabled: true
  - name: learn-night
    directive: learn
    at: "22:30"
    enabled: false
"""


def _day_core(tmp_path):
    from datetime import UTC, datetime

    from core.clock import fixed_clock
    return Core(llm=MockLLMClient(READING), repository=JsonRepository(tmp_path / "day.json"), canon="C", model="m",
                clock=fixed_clock(datetime(2026, 11, 18, 10, 0, tzinfo=UTC)), natal=NATAL, mood_enabled=True,
                theme_descriptions={"quiet-collapse": "вигорання"})


def _schedule_client(tmp_path, core=None, *, scheduler=True):
    import dataclasses

    from core.config import load_config
    from server.brain import BrainHost
    (tmp_path / "schedule.yaml").write_text(SCHEDULE_YAML, encoding="utf-8")
    core = core or _day_core(tmp_path)
    client = TestClient(create_app(core, token=TOKEN, env="dev", version="4.4.0"))
    cfg = dataclasses.replace(load_config(load_env=False), scheduler=scheduler, schedule_path=tmp_path / "schedule.yaml",
                              schedule_state_path=tmp_path / "schedule.state", schedule_overrides=(),
                              sched_day_cap=24, thoughts_quiet_hours=(1, 7))
    client.app.state.brain = BrainHost(client.app.state.service, cfg)
    return client


def test_v44_today_answers_with_its_shape_and_never_computes_the_mood(tmp_path):
    core = _day_core(tmp_path)
    client = TestClient(create_app(core, token=TOKEN, env="dev", version="4.4.0"))
    res = client.get("/v1/today", headers=AUTH)
    assert res.status_code == 200 and res.headers["cache-control"] == "no-store"
    body = res.json()
    assert set(body) == {"date", "mood", "biorhythms", "closeness", "plan", "cycle"}
    assert body["mood"] is None and core._llm.calls == []  # not computed yet, and the route didn't compute it
    assert [b["name"] for b in body["biorhythms"]] == ["physical", "emotional", "intellectual"]
    assert set(body["biorhythms"][0]) == {"name", "value", "label", "trend", "period", "day"}
    assert set(body["closeness"]) == {"level", "name"} and body["plan"] is None
    core.ensure_mood()
    mood = client.get("/v1/today", headers=AUTH).json()["mood"]
    assert mood == {"resolution": "Зосереджена, без поспіху.", "reading": "Тиха ніч.", "theme": "quiet-collapse",
                    "made_at": None}


def test_v44_the_schedule_answers_with_every_entry(tmp_path):
    client = _schedule_client(tmp_path)
    res = client.get("/v1/schedule", headers=AUTH)
    assert res.status_code == 200 and res.headers["cache-control"] == "no-store"
    body = res.json()
    assert set(body) == {"now", "quiet_hours", "day_cap", "fired_today", "entries"}
    assert body["quiet_hours"] == [1, 7] and body["day_cap"] == 24 and body["fired_today"] == 0
    rows = {e["name"]: e for e in body["entries"]}
    assert list(rows) == ["muse-silent", "morning-brief", "learn-night"]
    assert set(rows["muse-silent"]) == {"name", "directive", "seeds", "topic", "seed_lines", "show", "enabled",
                                        "trigger", "last_fired", "next_due", "if_silent"}
    assert isinstance(rows["muse-silent"]["seed_lines"], list) and rows["morning-brief"]["seed_lines"] is None
    assert rows["muse-silent"]["if_silent"] is True and rows["muse-silent"]["trigger"]["kind"] == "idle"
    assert rows["morning-brief"]["trigger"] == {"kind": "at", "interval_s": None, "at": "08:00", "days": [],
                                                "window": None, "cron": None}
    assert rows["learn-night"]["enabled"] is False and rows["learn-night"]["next_due"] is None
    assert client.get("/v1/state", headers=AUTH).json()["features"]["schedule"] is True


def test_v44_no_scheduler_no_schedule(tmp_path):
    off = _schedule_client(tmp_path, scheduler=False)
    assert off.get("/v1/schedule", headers=AUTH).status_code == 404
    assert off.get("/v1/state", headers=AUTH).json()["features"]["schedule"] is False
    assert _client(tmp_path / "bare").get("/v1/schedule", headers=AUTH).status_code == 404


def test_v44_the_reads_are_the_owners_token_only(tmp_path):
    from tests._agora_world import OWNER_AUTH, ROOM_AUTH, agora_client
    client = agora_client(tmp_path, core=_day_core(tmp_path))
    service = client.app.state.service
    service.schedule_view = lambda now: {"now": None, "quiet_hours": None, "day_cap": 0, "fired_today": 0,
                                         "entries": []}
    for path in ("/v1/today", "/v1/schedule"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.get(path, headers=ROOM_AUTH).status_code == 403  # the room never reads her day
        assert client.get(path, headers=OWNER_AUTH).status_code == 200


def test_v44_the_reads_never_wait_for_the_turn_lock_and_write_nothing(tmp_path):
    from core.schedule import save_state
    client = _schedule_client(tmp_path)
    state_path = tmp_path / "schedule.state"
    save_state(state_path, {"brief#at|0|(8, 0)|()|None|##": "2026-11-18T08:00:03+00:00"})
    store = tmp_path / "day.json"
    before = (state_path.read_bytes(), store.read_bytes() if store.exists() else None)
    service = client.app.state.service
    assert service.lock.acquire(timeout=1)  # a turn that runs for minutes holds it
    try:
        assert client.get("/v1/today", headers=AUTH).status_code == 200
        assert client.get("/v1/schedule", headers=AUTH).status_code == 200
    finally:
        service.lock.release()
    assert (state_path.read_bytes(), store.read_bytes() if store.exists() else None) == before


def _tools_core(tmp_path, *, trace, script):
    root = tmp_path / "files" / "owner"
    root.mkdir(parents=True, exist_ok=True)
    (root / "note.md").write_text("рядок", encoding="utf-8")
    return Core(llm=MockLLMClient(states={"reply": "ось", "emotion": "calm", "intensity": 0.5}, tool_script=script),
                repository=JsonRepository(tmp_path / f"t-{trace}.json"), canon="C", model="m",
                files_dir=tmp_path / "files", user_id="owner", mood_enabled=False, closeness_enabled=False,
                thoughts_enabled=False, file_tool_enabled=True, file_tool_trace=trace, tool_max_steps=30)


def test_v44_the_outcome_lists_the_turns_tools_while_the_trace_is_on(tmp_path):
    script = [("list_files", {}), ("read_file", {"path": "note.md"})]
    on = TestClient(create_app(_tools_core(tmp_path, trace=True, script=script), token=TOKEN))
    calls = on.post("/v1/turn", json={"text": "?"}, headers=AUTH).json()["tool_calls"]
    assert [c["name"] for c in calls] == ["list_files", "read_file"]
    assert set(calls[1]) == {"name", "input", "result"} and calls[1]["input"] == "path=note.md"
    streamed = TestClient(create_app(_tools_core(tmp_path / "s", trace=True, script=script), token=TOKEN))
    res = streamed.post("/v1/turn/stream", json={"text": "?", "turn_id": "t-1"}, headers=AUTH)
    assert [c["name"] for c in _events(res.text)[-1][1]["tool_calls"]] == ["list_files", "read_file"]
    quiet = TestClient(create_app(_tools_core(tmp_path / "q", trace=True, script=[]), token=TOKEN))
    assert quiet.post("/v1/turn", json={"text": "?"}, headers=AUTH).json()["tool_calls"] == []  # none called


def test_v44_without_the_trace_the_outcome_is_byte_identical(tmp_path):
    off = TestClient(create_app(_tools_core(tmp_path, trace=False, script=[("list_files", {})]), token=TOKEN))
    res = off.post("/v1/turn", json={"text": "?"}, headers=AUTH).json()
    assert "tool_calls" not in res
    assert set(res) == {"reply", "emotion", "intensity", "thinking", "intent", "style", "stats", "timing", "state"}


def test_v44_the_tool_list_is_bounded(tmp_path):
    from core.agent import TOOL_CALLS_MAX, TOOL_INPUT_CHARS, TOOL_RESULT_CHARS
    script = [("create_file", {"path": f"a{i}.md", "content": "слово " * 400}) for i in range(TOOL_CALLS_MAX + 3)]
    client = TestClient(create_app(_tools_core(tmp_path, trace=True, script=script), token=TOKEN))
    calls = client.post("/v1/turn", json={"text": "?"}, headers=AUTH).json()["tool_calls"]
    assert len(calls) == TOOL_CALLS_MAX
    assert all(len(c["input"]) <= TOOL_INPUT_CHARS and len(c["result"]) <= TOOL_RESULT_CHARS for c in calls)
    assert all("\n" not in c["input"] and "\n" not in c["result"] for c in calls)
    assert calls[0]["input"].startswith("path=a0.md content=слово") and calls[0]["input"].endswith("…")


def test_v44_the_hormonal_cycle_on_today_and_on_the_state(tmp_path):
    from datetime import UTC, datetime

    from core.clock import fixed_clock
    natal = NATAL + "\nЦикл: день 1 — 25.05.2026, довжина 28 днів."
    core = Core(llm=MockLLMClient(READING), repository=JsonRepository(tmp_path / "c.json"), canon="C", model="m",
                clock=fixed_clock(datetime(2026, 11, 19, 10, 0, tzinfo=UTC)), natal=natal, mood_enabled=True,
                cycle_enabled=True)
    client = TestClient(create_app(core, token=TOKEN, env="dev", version="4.4.0"))
    cycle = client.get("/v1/today", headers=AUTH).json()["cycle"]
    assert set(cycle) == {"day", "length", "phase", "note", "phases"} and cycle["length"] == 28
    assert len(cycle["phases"]) == 28 and cycle["phases"][cycle["day"] - 1] == cycle["phase"]
    assert client.get("/v1/state", headers=AUTH).json()["cycle"] == {k: cycle[k] for k in ("phase", "day", "length")}
    off = TestClient(create_app(_day_core(tmp_path / "off"), token=TOKEN))  # no «Цикл:» anchor
    assert off.get("/v1/today", headers=AUTH).json()["cycle"] is None
    assert off.get("/v1/state", headers=AUTH).json()["cycle"] is None


# --- v4.5 LUMI-322: the owner's actions — create-only, nothing deleted, every action logged -------------------------

def _actions_client(tmp_path, *, script=None, image=True):
    root = _gallery_sandbox(tmp_path / "files")
    core = Core(llm=MockLLMClient(states={"reply": "бачу", "emotion": "calm", "intensity": 0.5},
                                  tool_script=script),
                repository=JsonRepository(tmp_path / "a.json"), canon="C", model="m", files_dir=tmp_path / "files",
                user_id="owner", mood_enabled=False, closeness_enabled=False, thoughts_enabled=False,
                image_enabled=image)
    client = TestClient(create_app(core, token=TOKEN, env="dev", version="4.5.0", actions_log=tmp_path / "actions.jsonl"))
    return client, root, core


def _log_lines(tmp_path):
    path = tmp_path / "actions.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_v45_upload_is_create_only_and_logged(tmp_path):
    client, root, _ = _actions_client(tmp_path)
    before = _fingerprint(root)
    png = _real_png((20, 20))
    res = client.post("/v1/files/upload", params={"path": "art", "name": "p.png"}, content=png,
                      headers={**AUTH, "X-Lumi-Client": "w1"})
    assert res.status_code == 200 and res.headers["cache-control"] == "no-store"
    assert res.json() == {"path": "art/p (2).png", "size": len(png)}
    assert [row for row in _fingerprint(root) if not row[0].endswith("p (2).png")] == before  # nothing overwritten
    text = client.post("/v1/files/upload", params={"name": "лист.md"}, content="# Привіт\n".encode(), headers=AUTH)
    assert text.json()["path"] == "лист.md"
    lines = _log_lines(tmp_path)
    assert [line["action"] for line in lines] == ["upload", "upload"] and lines[0]["client"] == "w1"
    assert "Привіт" not in (tmp_path / "actions.jsonl").read_text(encoding="utf-8")  # no content in the log
    assert client.get("/v1/state", headers=AUTH).json()["features"]["actions"] is True


def test_v45_upload_refusals(tmp_path, monkeypatch):
    import core.actions as actions
    client, root, _ = _actions_client(tmp_path)
    cases = [({"name": "x.svg"}, b"<svg onload=x>\x00", 415), ({"name": "x.bin"}, bytes(range(256)), 415),
             ({"path": "nope", "name": "a.txt"}, b"a", 404), ({"path": "../out", "name": "a.txt"}, b"a", 400),
             ({"name": "../a.txt"}, b"a", 400), ({"name": ".hidden"}, b"a", 400)]
    for params, body, status in cases:
        res = client.post("/v1/files/upload", params=params, content=body, headers=AUTH)
        assert res.status_code == status, (params, res.status_code)
        assert str(tmp_path) not in res.text
    monkeypatch.setattr(actions, "UPLOAD_MAX_TEXT", 4)
    assert client.post("/v1/files/upload", params={"name": "a.txt"}, content=b"12345", headers=AUTH).status_code == 413
    import server.app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_MAX_PICTURE", 64)  # the streaming cap
    big = client.post("/v1/files/upload", params={"name": "b.png"}, content=_real_png((40, 40)), headers=AUTH)
    assert big.status_code == 413 and not (root / "b.png").exists()
    assert _log_lines(tmp_path) == []  # a refusal logs nothing


def test_v45_rename_trash_restore_and_download(tmp_path):
    client, root, _ = _actions_client(tmp_path)
    assert client.post("/v1/files/rename", json={"path": "notes/ideas.md", "name": "думки.md"},
                       headers=AUTH).json() == {"path": "notes/ideas.md", "to": "notes/думки.md"}
    (root / "notes" / "b.md").write_text("b", encoding="utf-8")
    clash = client.post("/v1/files/rename", json={"path": "notes/b.md", "name": "думки.md"}, headers=AUTH)
    assert clash.status_code == 409
    moved = client.post("/v1/files/trash", json={"path": "notes/думки.md"}, headers=AUTH).json()
    assert moved["to"].startswith(".trash/") and moved["to"].endswith("/думки.md")
    assert not (root / "notes" / "думки.md").exists()
    back = client.post("/v1/files/restore", json={"path": moved["to"]}, headers=AUTH).json()
    assert back == {"path": moved["to"], "to": "notes/думки.md"}
    assert (root / "notes" / "думки.md").read_text(encoding="utf-8").startswith("# Ідеї")
    unknown = client.post("/v1/files/restore", json={"path": ".trash/2026-01-01/x.md"}, headers=AUTH)
    assert unknown.status_code in (404, 409)
    dl = client.get("/v1/files/download", params={"path": "notes/думки.md"}, headers=AUTH)
    assert dl.status_code == 200 and dl.content == (root / "notes" / "думки.md").read_bytes()
    assert dl.headers["content-type"] == "application/octet-stream"
    assert dl.headers["content-disposition"].startswith("attachment;") and "UTF-8''" in dl.headers["content-disposition"]
    assert dl.headers["x-content-type-options"] == "nosniff"
    assert client.get("/v1/files/download", params={"path": "notes"}, headers=AUTH).status_code == 404
    assert client.get("/v1/files/download", params={"path": "../x"}, headers=AUTH).status_code == 400
    lines = _log_lines(tmp_path)
    assert [line["action"] for line in lines] == ["rename", "trash", "restore", "download"]  # a refusal logs nothing
    assert lines[-1]["path"] == "notes/думки.md" and lines[-1]["size"] == len(dl.content)  # code review #1


def test_v45_a_trashed_picture_leaves_the_gallery(tmp_path):
    client, root, _ = _actions_client(tmp_path)
    client.app.state.service.thumbs_dir = tmp_path / "cache"
    client.post("/v1/files/trash", json={"path": "art/p.png"}, headers=AUTH)
    paths = [i["path"] for i in client.get("/v1/images", headers=AUTH).json()["items"]]
    assert paths == ["art/q.png"]


def test_v45_the_writes_are_the_owners_token_only(tmp_path):
    from tests._agora_world import OWNER_AUTH, ROOM_AUTH, agora_client
    root = _gallery_sandbox(tmp_path / "files")
    client = agora_client(tmp_path, core=_files_core(tmp_path), actions_log=tmp_path / "actions.jsonl")
    calls = [("post", "/v1/files/upload", {"params": {"name": "a.txt"}, "content": b"a"}),
             ("post", "/v1/files/rename", {"json": {"path": "art/p.png", "name": "z.png"}}),
             ("post", "/v1/files/trash", {"json": {"path": "art/q.png"}}),
             ("post", "/v1/files/restore", {"json": {"path": ".trash/x/y.png"}}),
             ("get", "/v1/files/download", {"params": {"path": "art/p.png"}})]
    before = _fingerprint(root)
    for method, path, kw in calls:
        assert getattr(client, method)(path, **kw).status_code == 401
        assert getattr(client, method)(path, headers={"Authorization": "Bearer wrong"}, **kw).status_code == 401
        assert getattr(client, method)(path, headers=ROOM_AUTH, **kw).status_code == 403  # the room never writes
    assert _fingerprint(root) == before and not (tmp_path / "actions.jsonl").exists()
    assert client.get("/v1/files/download", params={"path": "art/p.png"}, headers=OWNER_AUTH).status_code == 200


def test_v45_a_write_never_waits_for_the_turn_lock(tmp_path):
    client, _, _ = _actions_client(tmp_path)
    service = client.app.state.service
    assert service.lock.acquire(timeout=1)
    try:
        assert client.post("/v1/files/upload", params={"name": "a.txt"}, content=b"a", headers=AUTH).status_code == 200
    finally:
        service.lock.release()


def test_v45_no_action_log_no_actions(tmp_path):
    _gallery_sandbox(tmp_path / "files")
    client = TestClient(create_app(_files_core(tmp_path), token=TOKEN))
    assert client.get("/v1/state", headers=AUTH).json()["features"]["actions"] is False
    assert client.post("/v1/files/upload", params={"name": "a.txt"}, content=b"a", headers=AUTH).status_code == 404


def test_v45_a_turn_shows_her_a_picture_of_her_sandbox(tmp_path):
    client, root, core = _actions_client(tmp_path)
    holder: dict = {}
    heard = _heard(client, lambda: holder.update(res=client.post(
        "/v1/turn", json={"text": "що тут?", "sandbox_images": ["art/p.png"]}, headers=AUTH)))
    assert holder["res"].status_code == 200
    seen = core._llm.images_seen
    assert len(seen) == 1 and seen[0]["media_type"] == "image/png"
    import base64
    assert base64.b64decode(seen[0]["data"]) == (root / "art" / "p.png").read_bytes()
    mine = [d for e, d in heard if e == "message" and d["role"] == "user"]
    assert mine[-1]["pictures"] == ["art/p.png"]  # your line carries the picture you showed her
    streamed = client.post("/v1/turn/stream", json={"text": "а тут?", "sandbox_images": ["art/q.png"], "turn_id": "s1"},
                           headers=AUTH)
    assert _events(streamed.text)[-1][0] == "done" and len(core._llm.images_seen) == 2


def test_v45_sandbox_images_refusals(tmp_path):
    client, _, core = _actions_client(tmp_path)
    for body, status in (({"text": "?", "sandbox_images": ["../x.png"]}, 400),
                         ({"text": "?", "sandbox_images": ["art/none.png"]}, 404),
                         ({"text": "?", "sandbox_images": ["notes/ideas.md"]}, 415),
                         ({"text": "?", "sandbox_images": ["art/p.png"] * 5}, 400)):
        res = client.post("/v1/turn", json=body, headers=AUTH)
        assert res.status_code == status, (body, res.status_code)
    assert core._llm.calls == []  # refused before any turn ran


def test_v45_the_room_never_shows_her_a_sandbox_picture(tmp_path):
    from tests._agora_world import ROOM_AUTH, agora_client
    _gallery_sandbox(tmp_path / "files")
    client = agora_client(tmp_path, core=_files_core(tmp_path))
    res = client.post("/v1/turn", json={"text": "?", "channel": "agora", "turn_id": "r1", "speaker": "Ада",
                                        "sandbox_images": ["art/p.png"]}, headers=ROOM_AUTH)
    assert res.status_code == 422


def test_v45_with_vision_off_a_sandbox_picture_is_refused_not_lost(tmp_path):
    client, _, core = _actions_client(tmp_path, image=False)
    res = client.post("/v1/turn", json={"text": "?", "sandbox_images": ["art/p.png"]}, headers=AUTH)
    assert res.status_code == 422 and "image.enabled" in res.json()["detail"]
    assert core._llm.calls == []


def test_review_v45_1_a_download_and_a_shown_picture_are_logged_once(tmp_path):
    """v4.5 code review #1: the DoD's "every action is in actions.jsonl" — a download and each picture shown to her
    leave a line too (no content), a retried turn_id logs nothing again, and without a log they are still served."""
    client, _, _ = _actions_client(tmp_path)
    body = {"text": "що тут?", "sandbox_images": ["art/p.png", "art/q.png"], "turn_id": "t-show"}
    assert client.post("/v1/turn", json=body, headers={**AUTH, "X-Lumi-Client": "w1"}).status_code == 200
    assert client.post("/v1/turn", json=body, headers=AUTH).status_code == 200  # the stored outcome, not a rerun
    streamed = {"text": "а це?", "sandbox_images": ["art/q.png"], "turn_id": "s-show"}
    assert _events(client.post("/v1/turn/stream", json=streamed, headers=AUTH).text)[-1][0] == "done"
    lines = _log_lines(tmp_path)
    assert [(line["action"], line["path"]) for line in lines] == [("show", "art/p.png"), ("show", "art/q.png"),
                                                                 ("show", "art/q.png")]
    assert lines[0]["client"] == "w1" and all(set(line) <= {"ts", "action", "path", "to", "size", "client"}
                                              for line in lines)
    _gallery_sandbox(tmp_path / "bare" / "files")
    bare = TestClient(create_app(_files_core(tmp_path / "bare"), token=TOKEN))  # no action log configured
    assert bare.get("/v1/files/download", params={"path": "art/p.png"}, headers=AUTH).status_code == 200


# --- v4.6 LUMI-328: her voice — the audio route, the switch, the ids and the voice push --------------------------

def _voice_setup(tmp_path, *, tts="mock", first=True):
    from server.voice import VoiceSetup
    from voice.tts import MockTTS

    tts = MockTTS() if tts == "mock" else tts
    return VoiceSetup(tts=tts, root=tmp_path / "voice", switch_path=tmp_path / "voice.json", first=first), tts


def _voice_client(tmp_path, **kw):
    setup, tts = _voice_setup(tmp_path, **kw)
    core = Core(llm=MockLLMClient(states={"reply": "так", "emotion": "tender", "intensity": 0.5}),
                repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m")
    return TestClient(create_app(core, token=TOKEN, env="dev", version="4.6.0", voice=setup)), tts


def _bus_events(sub):
    out = []
    while not sub.empty():
        item = sub.get_nowait()
        out.append((item[0], item[1]))
    return out


def test_v46_the_state_carries_the_switch_only_where_the_server_can_voice(tmp_path):
    client, _ = _voice_client(tmp_path)
    assert client.get("/v1/state", headers=AUTH).json()["voice"] == {"enabled": True, "available": True}
    assert "voice" not in _client(tmp_path / "bare").get("/v1/state", headers=AUTH).json()  # no setup: absent
    keyless, _ = _voice_client(tmp_path / "k", tts=None)
    assert keyless.get("/v1/state", headers=AUTH).json()["voice"] == {"enabled": False, "available": False}


def test_v46_a_client_turn_names_its_voice_and_the_voice_push_follows_the_message(tmp_path):
    client, tts = _voice_client(tmp_path)
    service = client.app.state.service
    subs = [service.bus.subscribe(), service.bus.subscribe()]  # two listeners — still one generation
    res = client.post("/v1/turn", json={"text": "?"}, headers=AUTH).json()
    assert service.voicer.drain(5)
    vid = res["voice"]
    assert len(vid) == 32 and tts.calls == [("так", "tender")]
    for sub in subs:
        events = [(e, d) for e, d in _bus_events(sub) if e in {"message", "voice"}]
        assert [e for e, _ in events] == ["message", "message", "voice"]
        assert events[1][1]["voice"] == vid and "voice" not in events[0][1]  # her line names it; yours never
        assert events[2][1] == {"message": vid, "ready": True}
    audio = client.get(f"/v1/voice/{vid}", headers=AUTH)
    assert audio.status_code == 200 and audio.content == b"AUDIO:" + "так".encode()
    assert audio.headers["content-type"] == "audio/mpeg" and audio.headers["x-content-type-options"] == "nosniff"


def test_v46_the_streamed_outcome_names_the_voice_too(tmp_path):
    client, _ = _voice_client(tmp_path)
    done = _events(client.post("/v1/turn/stream", json={"text": "?", "turn_id": "v-1"}, headers=AUTH).text)[-1]
    assert done[0] == "done" and len(done[1]["voice"]) == 32


def test_v46_with_the_switch_off_nothing_is_voiced_and_the_shapes_are_v45s(tmp_path):
    client, tts = _voice_client(tmp_path, first=False)
    service = client.app.state.service
    sub = service.bus.subscribe()
    res = client.post("/v1/turn", json={"text": "?"}, headers=AUTH).json()
    assert set(res) == {"reply", "emotion", "intensity", "thinking", "intent", "style", "stats", "timing", "state"}
    events = _bus_events(sub)
    assert "voice" not in [e for e, _ in events]
    assert all("voice" not in d for e, d in events if e == "message")
    assert tts.calls == []


def test_v46_a_failed_generation_leaves_the_text_and_says_so(tmp_path):
    class Broken:
        calls = 0

        def synth(self, text, *, emotion=None):
            Broken.calls += 1
            raise RuntimeError("network down")

    client, _ = _voice_client(tmp_path, tts=Broken())
    service = client.app.state.service
    sub = service.bus.subscribe()
    res = client.post("/v1/turn", json={"text": "?"}, headers=AUTH)
    assert res.status_code == 200 and res.json()["reply"] == "так"  # the turn is unchanged
    service.voicer.drain(5)
    assert ("voice", {"message": res.json()["voice"], "ready": False}) in _bus_events(sub)
    assert client.get(f"/v1/voice/{res.json()['voice']}", headers=AUTH).status_code == 404


def test_v46_a_telegram_turn_and_a_spoken_turn_are_voiced_and_pushed(tmp_path):
    import dataclasses

    from core.config import load_config
    from server.app import Outbox
    from server.brain import BrainHost
    from state import fifo

    setup, tts = _voice_setup(tmp_path)
    core = Core(llm=MockLLMClient(states={"reply": "так", "emotion": "joy", "intensity": 0.5}),
                repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m")
    app = create_app(core, token=TOKEN, outbox=Outbox(tmp_path / "outbox.jsonl"), voice=setup)
    cfg = dataclasses.replace(load_config(load_env=False), bridge=True, inbox_path=tmp_path / "inbox.jsonl",
                              outbox_path=tmp_path / "outbox.jsonl", scheduler=False, thoughts=False,
                              idle_nudge=False)
    fifo.append(tmp_path / "inbox.jsonl", "привіт", source="telegram")
    service = app.state.service
    sub = service.bus.subscribe()
    host = BrainHost(service, cfg)
    host.inbox_tick(host._clock())
    with service.background():
        host._spoken_turn("seed", source="nudge")
    assert service.voicer.drain(5)
    events = [(e, d) for e, d in _bus_events(sub) if e in {"message", "voice"}]
    lili = [d for e, d in events if e == "message" and d["role"] == "lili"]
    assert [d["source"] for d in lili] == ["telegram", "nudge"] and len(tts.calls) == 2
    for line in lili:  # each line's voice push comes after its message
        at = events.index(("message", line))
        assert ("voice", {"message": line["voice"], "ready": True}) in events[at + 1:]
    assert [r["voice"] for r in fifo.read_since(tmp_path / "outbox.jsonl", 0)] == [f"{d['voice']}.mp3" for d in lili]


def test_v46_a_reconnecting_listener_gets_the_voice_push_replayed(tmp_path):
    client, _ = _voice_client(tmp_path)
    service = client.app.state.service
    vid = client.post("/v1/turn", json={"text": "?"}, headers=AUTH).json()["voice"]
    service.voicer.drain(5)
    replayed = _bus_events(service.bus.subscribe(replay=True))
    assert ("voice", {"message": vid, "ready": True, "replay": True}) in replayed


def test_v46_the_audio_route_serves_only_a_known_line(tmp_path):
    client, _ = _voice_client(tmp_path)
    service = client.app.state.service
    vid = "ab" * 16
    service.voice_store.write("owner", vid, b"MP3")
    assert client.get(f"/v1/voice/{vid}", headers=AUTH).content == b"MP3"
    for bad in ("cd" * 16, "AB" * 16, "ab" * 15, "..%2Fvoice.json", "x.mp3"):
        assert client.get(f"/v1/voice/{bad}", headers=AUTH).status_code == 404
    assert client.get(f"/v1/voice/{vid}").status_code == 401
    assert _client(tmp_path / "bare").get(f"/v1/voice/{vid}", headers=AUTH).status_code == 404


def test_v46_the_switch_persists_and_is_refused_without_a_voice(tmp_path):
    client, tts = _voice_client(tmp_path)
    assert client.post("/v1/voice", json={"enabled": False}, headers=AUTH).json() == {"enabled": False,
                                                                                      "available": True}
    assert client.get("/v1/state", headers=AUTH).json()["voice"]["enabled"] is False
    restarted, _ = _voice_client(tmp_path)  # a new app on the same data root: the switch survives
    assert restarted.get("/v1/state", headers=AUTH).json()["voice"]["enabled"] is False
    restarted.post("/v1/turn", json={"text": "?"}, headers=AUTH)
    assert tts.calls == []
    assert restarted.post("/v1/voice", json={"enabled": True}, headers=AUTH).json()["enabled"] is True
    keyless, _ = _voice_client(tmp_path / "k", tts=None)
    res = keyless.post("/v1/voice", json={"enabled": True}, headers=AUTH)
    assert res.status_code == 409 and "no ElevenLabs key or voice id" in res.json()["detail"]
    assert not (tmp_path / "k" / "voice.json").exists()
    assert _client(tmp_path / "bare").post("/v1/voice", json={"enabled": True}, headers=AUTH).status_code == 404
    assert client.post("/v1/voice", json={"enabled": True}).status_code == 401


def test_v46_the_switch_never_waits_for_the_turn_lock(tmp_path):
    client, _ = _voice_client(tmp_path)
    service = client.app.state.service
    assert service.lock.acquire(timeout=1)
    try:
        assert client.post("/v1/voice", json={"enabled": False}, headers=AUTH).status_code == 200
        assert client.get(f"/v1/voice/{'ab' * 16}", headers=AUTH).status_code == 404
    finally:
        service.lock.release()


def test_v46_the_room_never_reaches_her_voice(tmp_path):
    from tests._agora_world import ROOM_AUTH, agora_client

    setup, tts = _voice_setup(tmp_path)
    client = agora_client(tmp_path, voice=setup)
    vid = "ab" * 16
    client.app.state.service.voice_store.write("owner", vid, b"MP3")
    assert client.get(f"/v1/voice/{vid}", headers=ROOM_AUTH).status_code == 403
    assert client.post("/v1/voice", json={"enabled": False}, headers=ROOM_AUTH).status_code == 403
    assert client.app.state.service.voice_switch.enabled is True  # unchanged
    res = client.post("/v1/turn", json={"text": "привіт", "channel": "agora", "turn_id": "r1", "speaker": "Ада"},
                       headers=ROOM_AUTH)
    assert res.status_code == 200
    assert client.app.state.service.voicer.drain(5) and tts.calls == []  # the room's lines are never voiced


# --- v4.7 LUMI-332: /v1/dictation ---------------------------------------------------------------------------------

DICT_PCM = b"\x01\x00" * 160
DICT_STOP = json.dumps({"type": "stop"})


class _EchoStream:
    """Deepgram's side for the contract: each audio frame → an interim; CloseStream → the phrase, then the end."""

    def __init__(self):
        import asyncio

        self.queue = asyncio.Queue()

    async def send_pcm(self, data):
        self.queue.put_nowait({"type": "Results", "is_final": False,
                               "channel": {"alternatives": [{"transcript": "Привіт"}]}})

    async def finish(self):
        self.queue.put_nowait({"type": "Results", "is_final": True, "speech_final": True,
                               "channel": {"alternatives": [{"transcript": "Привіт, світ."}]}})
        self.queue.put_nowait(None)

    async def close(self):
        pass

    async def events(self):
        while (event := await self.queue.get()) is not None:
            yield event


def _dictation(*, stream=True, text="Так."):
    from server.dictation import DictationSetup
    from voice.stt import MockSTT

    async def factory():
        return _EchoStream()

    return DictationSetup(stt=MockSTT(text), stream_factory=factory if stream else None, lang="uk")


def _dictation_client(tmp_path, **kw):
    core = Core(llm=MockLLMClient(states={"reply": "так", "emotion": "calm", "intensity": 0.5}),
                repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m")
    return TestClient(create_app(core, token=TOKEN, env="dev", version="4.7.0", **kw))


def _refused(client, headers=None):
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/v1/dictation", headers=headers or {}) as ws:
            ws.receive_json()
    return caught.value.code


def test_v47_dictation_streams_the_words_back_in_the_pinned_shapes(tmp_path):
    client = _dictation_client(tmp_path, dictation=_dictation())
    with client.websocket_connect("/v1/dictation", headers=AUTH) as ws:
        assert ws.receive_json() == {"type": "ready", "mode": "stream"}
        ws.send_bytes(DICT_PCM)
        assert ws.receive_json() == {"type": "interim", "text": "Привіт"}
        ws.send_text(DICT_STOP)
        assert ws.receive_json() == {"type": "final", "text": "Привіт, світ."}
        assert ws.receive_json() == {"type": "done"}


def test_v47_dictation_without_streaming_recognizes_the_whole_recording_on_the_stop(tmp_path):
    client = _dictation_client(tmp_path, dictation=_dictation(stream=False, text="Так, саме так."))
    with client.websocket_connect("/v1/dictation", headers=AUTH) as ws:
        assert ws.receive_json() == {"type": "ready", "mode": "batch"}
        ws.send_bytes(DICT_PCM)
        ws.send_text(DICT_STOP)
        assert ws.receive_json() == {"type": "final", "text": "Так, саме так."}
        assert ws.receive_json() == {"type": "done"}


def test_v47_dictation_is_the_owners_token_only(tmp_path):
    from tests._agora_world import OWNER_AUTH, ROOM_AUTH, agora_client

    client = agora_client(tmp_path, dictation=_dictation())
    assert _refused(client) == 1008  # no token: the handshake is refused
    assert _refused(client, {"Authorization": "Bearer wrong"}) == 1008
    assert _refused(client, ROOM_AUTH) == 1008  # the room never dictates
    with client.websocket_connect("/v1/dictation", headers=OWNER_AUTH) as ws:
        assert ws.receive_json()["type"] == "ready"


def test_v47_a_server_without_a_recognizer_refuses_dictation(tmp_path):
    client = _dictation_client(tmp_path)
    assert _refused(client, AUTH) == 1008
    assert client.get("/v1/state", headers=AUTH).json()["features"]["dictation"] is False


def test_v47_features_dictation_follows_the_recognizer(tmp_path):
    client = _dictation_client(tmp_path, dictation=_dictation())
    assert client.get("/v1/state", headers=AUTH).json()["features"]["dictation"] is True


def test_v47_one_dictation_at_a_time_and_never_the_turn_lock(tmp_path):
    client = _dictation_client(tmp_path, dictation=_dictation())
    with client.websocket_connect("/v1/dictation", headers=AUTH) as first:
        assert first.receive_json()["type"] == "ready"
        with client.websocket_connect("/v1/dictation", headers=AUTH) as second:
            assert second.receive_json() == {"type": "error", "detail": "busy"}
        assert client.post("/v1/turn", json={"text": "привіт"}, headers=AUTH).status_code == 200  # she still answers
        first.send_bytes(DICT_PCM)
        assert first.receive_json()["type"] == "interim"
        first.send_text(DICT_STOP)
        assert [first.receive_json()["type"] for _ in range(2)] == ["final", "done"]
    with client.websocket_connect("/v1/dictation", headers=AUTH) as again:  # the first one ended: free again
        assert again.receive_json()["type"] == "ready"

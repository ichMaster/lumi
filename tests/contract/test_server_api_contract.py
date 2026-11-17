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
    routes = {(m, r.path) for r in _client(tmp_path).app.routes if r.path.startswith("/v1/") for m in r.methods}
    assert routes == {
        ("GET", "/v1/health"), ("GET", "/v1/state"), ("POST", "/v1/turn"),
        ("POST", "/v1/command"), ("POST", "/v1/session/new"),
        ("POST", "/v1/turn/stream"),  # v2.3: the streamed turn (SSE)
        ("GET", "/v1/events"),  # v2.4: the push channel (SSE)
        ("POST", "/v1/directive"),  # v2.4: a typed %directive runs on the server
        ("GET", "/v1/files"), ("GET", "/v1/files/text"), ("GET", "/v1/files/raw"),  # v4.1: her sandbox, read-only
        ("GET", "/v1/images"), ("GET", "/v1/images/thumb"),  # v4.2: her pictures and their thumbnails, read-only
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
    }
    assert set(state["closeness"]) == {"level", "name"}  # the level by name — never the raw value
    assert state["features"] == {"image": False, "web_lookup": False, "files": False,  # a bare core: all off
                                 "gallery": False}  # v4.2
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

    on = {(m, r.path) for r in agora_client(tmp_path).app.routes if r.path.startswith("/v1/") for m in r.methods}
    off = {(m, r.path) for r in _client(tmp_path / "off").app.routes if r.path.startswith("/v1/") for m in r.methods}
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

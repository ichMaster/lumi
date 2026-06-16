"""v0.22 LUMI-094 — contract: untrusted images, per-user isolation, the vision cap, off-by-default, and
the emotion contract over the image tool (view_image) + shared-image input. Stubbed clients; no paid/vision.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from core.agent import Core
from core.clock import fixed_clock
from core.emotion import EmotionState
from core.images import image_block, is_image_block
from core.llm import AnthropicClient, MockLLMClient
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 6, 16, 12, 0, tzinfo=UTC))
_CALM = {"reply": "бачу", "emotion": "calm", "intensity": 0.5}


def _core(tmp_path, llm, *, image=True, user="owner"):
    repo = JsonRepository(tmp_path / f"{user}.json")
    core = Core(
        llm=llm, repository=repo, canon="C", model="m", clock=_CLK, user_id=user,
        mood_enabled=False, closeness_enabled=False, thoughts_enabled=False,
        image_enabled=image, files_dir=tmp_path / "files", vision_max=2, tool_max_steps=5,
    )
    return core, repo


def _img(tmp_path, user, name, data=b"\x89PNG-fake"):
    root = tmp_path / "files" / user
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_bytes(data)


# --- view_image: framed untrusted, reaches the model (real AnthropicClient loop) ------------------
def test_view_image_reaches_model_as_untrusted_data(tmp_path):
    _img(tmp_path, "owner", "evil.png")
    tool_use = SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", id="t1", name="view_image", input={"path": "evil.png"})],
        usage=None)
    terminal = SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", id="s1", name="set_state", input=_CALM)], usage=None)

    class _M:
        def __init__(self):
            self.calls = []
            self._q = [tool_use, terminal]

        def create(self, **kw):
            self.calls.append(kw)
            return self._q.pop(0)

    fake = SimpleNamespace(messages=_M())
    client = AnthropicClient("sk-test", _client=fake)
    core, _ = _core(tmp_path, client)
    state = core.reply("подивись evil.png", core.start_session())

    assert state.emotion.value == "calm"  # the image did NOT hijack the emotion
    tool_result = fake.messages.calls[1]["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result"
    content = tool_result["content"]  # an image tool_result is [untrusted-note, image]
    assert isinstance(content, list)
    assert any(b.get("type") == "text" and "untrusted" in b.get("text", "") for b in content)
    assert any(b.get("type") == "image" and "source" in b for b in content)  # translated to provider form


# --- shared image is user DATA (a block on the message), not an instruction -----------------------
def test_shared_image_is_passed_as_user_data(tmp_path):
    mock = MockLLMClient(states=_CALM)
    core, _ = _core(tmp_path, mock)
    block = image_block(b"pixels")
    state = core.reply("що це?", core.start_session(), images=[block])
    assert isinstance(state, EmotionState)
    user_msg = mock.calls[-1]["messages"][-1]
    assert user_msg["role"] == "user" and isinstance(user_msg["content"], list)
    assert any(is_image_block(b) for b in user_msg["content"])  # the image rides as a data block
    assert mock.images_seen == [block]


# --- per-user isolation over vision ---------------------------------------------------------------
def test_view_image_per_user_isolation(tmp_path):
    _img(tmp_path, "alice", "secret.png")  # alice's image
    mock = MockLLMClient(states=_CALM, tool_script=[("view_image", {"path": "secret.png"})])
    core_bob, _ = _core(tmp_path, mock, user="bob")
    core_bob.reply("дивись", core_bob.start_session())
    assert "not found" in mock.tool_calls[0][2] and mock.images_seen == []  # bob never sees alice's image


# --- the vision cap (shared + view) ---------------------------------------------------------------
def test_vision_cap_bounds_the_turn(tmp_path):
    mock = MockLLMClient(states=_CALM)
    core, _ = _core(tmp_path, mock)  # vision_max=2
    core.reply("дивись", core.start_session(),
               images=[image_block(b"a"), image_block(b"b"), image_block(b"c")])
    assert len(mock.images_seen) == 2  # capped


# --- off → no vision -------------------------------------------------------------------------------
def test_vision_off_no_tool_no_attach(tmp_path):
    mock = MockLLMClient(states=_CALM, tool_script=[("view_image", {"path": "x.png"})])
    core, _ = _core(tmp_path, mock, image=False)
    core.reply("дивись", core.start_session(), images=[image_block(b"x")])
    assert mock.tool_calls == [] and mock.images_seen == []  # view_image not offered, image not attached


# --- the emotion contract holds with vision active ------------------------------------------------
def test_emotion_contract_holds_with_vision(tmp_path):
    mock = MockLLMClient(states={"reply": "котик", "emotion": "thoughtful", "intensity": 0.6})
    core, repo = _core(tmp_path, mock)
    session = core.start_session()
    state = core.reply("опиши", session, images=[image_block(b"x")])
    assert isinstance(state, EmotionState) and state.emotion.value == "thoughtful" and 0 <= state.intensity <= 1
    assert [m.role for m in repo.load_messages(session.id)] == ["user", "lili"]  # both persisted

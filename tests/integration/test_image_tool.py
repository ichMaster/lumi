"""v0.22 LUMI-092 — the view_image tool wired into Core.reply (mock multimodal model, no paid calls)."""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.emotion import EmotionState
from core.images import is_image_block
from core.llm import MockLLMClient
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 6, 16, 12, 0, tzinfo=UTC))
_STATE = {"reply": "бачу", "emotion": "calm", "intensity": 0.5}


def _core(tmp_path, llm, *, image=False, user="owner") -> Core:
    return Core(
        llm=llm, repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.", model="m", clock=_CLK, user_id=user,
        mood_enabled=False, closeness_enabled=False, thoughts_enabled=False,
        image_enabled=image, files_dir=tmp_path / "files", vision_max=2, tool_max_steps=5,
    )


def _img(tmp_path, user, name, data=b"\x89PNG-fake"):
    root = tmp_path / "files" / user
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_bytes(data)


def test_turn_views_sandbox_image_when_on(tmp_path):
    _img(tmp_path, "owner", "cat.png")
    mock = MockLLMClient(states={"reply": "котик у шапці", "emotion": "tender", "intensity": 0.7},
                         tool_script=[("view_image", {"path": "cat.png"})])
    core = _core(tmp_path, mock, image=True)
    state = core.reply("що на cat.png?", core.start_session())

    assert isinstance(state, EmotionState) and state.emotion.value == "tender"
    assert mock.tool_calls[0][0] == "view_image"
    assert is_image_block(mock.tool_calls[0][2])      # view_image returned an image block (not a string)
    assert len(mock.images_seen) == 1                  # the image reached the model


def test_no_view_image_when_off(tmp_path):
    mock = MockLLMClient(states=_STATE, tool_script=[("view_image", {"path": "x.png"})])
    core = _core(tmp_path, mock, image=False)
    core.reply("подивись", core.start_session())
    assert mock.tool_calls == [] and mock.images_seen == []  # off → no tool offered


def test_view_image_errors_degrade(tmp_path):
    _img(tmp_path, "owner", "ok.png")
    mock = MockLLMClient(states=_STATE, tool_script=[
        ("view_image", {"path": "notes.txt"}),      # not an image type
        ("view_image", {"path": "../escape.png"}),  # traversal
        ("view_image", {"path": "missing.png"}),    # missing file
    ])
    core = _core(tmp_path, mock, image=True)
    state = core.reply("дивись", core.start_session())
    assert isinstance(state, EmotionState)
    r0, r1, r2 = (c[2] for c in mock.tool_calls)
    assert "not an image" in r0 and "traversal" in r1 and "not found" in r2
    assert mock.images_seen == []  # nothing loaded


def test_per_turn_vision_cap(tmp_path):
    for n in ("a.png", "b.png", "c.png"):
        _img(tmp_path, "owner", n)
    mock = MockLLMClient(states=_STATE,
                         tool_script=[("view_image", {"path": p}) for p in ("a.png", "b.png", "c.png")])
    core = _core(tmp_path, mock, image=True)  # vision_max=2
    core.reply("дивись усі", core.start_session())
    results = [c[2] for c in mock.tool_calls]
    assert is_image_block(results[0]) and is_image_block(results[1])  # first two loaded
    assert "limit reached" in results[2]                              # the 3rd over the cap


def test_view_is_bound_to_the_active_users_sandbox(tmp_path):
    _img(tmp_path, "alice", "secret.png")  # alice's image
    mock = MockLLMClient(states=_STATE, tool_script=[("view_image", {"path": "secret.png"})])
    core_bob = _core(tmp_path, mock, image=True, user="bob")  # bob's root has no secret.png
    core_bob.reply("дивись", core_bob.start_session())
    assert "not found" in mock.tool_calls[0][2] and mock.images_seen == []  # isolated

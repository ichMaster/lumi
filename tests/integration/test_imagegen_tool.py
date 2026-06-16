"""v0.23 LUMI-096 — generate_image wired into Core.reply (mock model + stub ImageGen, no paid calls)."""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.emotion import EmotionState
from core.imagegen import ImageGenError
from core.llm import MockLLMClient
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 6, 17, 12, 0, tzinfo=UTC))
_STATE = {"reply": "ось, тримай", "emotion": "playful", "intensity": 0.7}


def _gen(data=b"\x89PNG-fake"):
    calls: list = []

    def gen(prompt: str, *, size: int = 768) -> bytes:
        calls.append((prompt, size))
        return data

    gen.calls = calls  # type: ignore[attr-defined]
    return gen


def _core(tmp_path, llm, *, image=False, image_gen=None, image_show="path,viewer,telegram", user="owner"):
    return Core(
        llm=llm, repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.", model="m", clock=_CLK, user_id=user,
        mood_enabled=False, closeness_enabled=False, thoughts_enabled=False,
        image_enabled=image, files_dir=tmp_path / "files", image_max_gen=2, image_show=image_show,
        image_signal_path=tmp_path / "image.txt", image_gen=image_gen, tool_max_steps=5,
    )


def test_turn_generates_and_shows_image_when_on(tmp_path):
    gen = _gen(b"\x89PNG-art")
    mock = MockLLMClient(states=_STATE, tool_script=[("generate_image", {"prompt": "кіт в окулярах"})])
    core = _core(tmp_path, mock, image=True, image_gen=gen)
    state = core.reply("намалюй кота", core.start_session())

    assert isinstance(state, EmotionState) and state.emotion.value == "playful"
    assert mock.tool_calls[0][0] == "generate_image" and "created art/" in mock.tool_calls[0][2]
    saved = list((tmp_path / "files" / "owner" / "art").glob("*.png"))
    assert len(saved) == 1 and saved[0].read_bytes() == b"\x89PNG-art"
    assert str(saved[0]) == (tmp_path / "image.txt").read_text(encoding="utf-8")  # display signal emitted
    assert gen.calls[0][0] == "кіт в окулярах"  # prompt reached the generator unchanged (no personal data)


def test_no_generate_when_off(tmp_path):
    mock = MockLLMClient(states=_STATE, tool_script=[("generate_image", {"prompt": "x"})])
    core = _core(tmp_path, mock, image=False, image_gen=_gen())
    core.reply("намалюй", core.start_session())
    assert mock.tool_calls == []  # off → no generate_image offered


def test_per_turn_generation_cap(tmp_path):
    mock = MockLLMClient(states=_STATE,
                         tool_script=[("generate_image", {"prompt": p}) for p in ("a", "b", "c")])
    core = _core(tmp_path, mock, image=True, image_gen=_gen())  # image_max_gen=2
    core.reply("намалюй три", core.start_session())
    results = [c[2] for c in mock.tool_calls]
    assert results[0].startswith("created") and results[1].startswith("created")
    assert "limit reached" in results[2]  # the 3rd over the cap


def test_generation_error_degrades(tmp_path):
    def boom(prompt, *, size=768):
        raise ImageGenError("safety refusal")
    mock = MockLLMClient(states=_STATE, tool_script=[("generate_image", {"prompt": "заборонене"})])
    core = _core(tmp_path, mock, image=True, image_gen=boom)
    state = core.reply("намалюй", core.start_session())
    assert isinstance(state, EmotionState)
    assert mock.tool_calls[0][2].startswith("error: image generation failed")
    assert not (tmp_path / "image.txt").exists()  # nothing shown on a failed generation


def test_display_path_only_emits_no_signal(tmp_path):
    mock = MockLLMClient(states=_STATE, tool_script=[("generate_image", {"prompt": "кіт"})])
    core = _core(tmp_path, mock, image=True, image_gen=_gen(), image_show="path")
    core.reply("намалюй", core.start_session())
    assert mock.tool_calls[0][2].startswith("created")  # the file is named in the result (path)
    assert not (tmp_path / "image.txt").exists()         # "path"-only → no viewer/telegram signal


def test_generate_bound_to_the_active_users_sandbox(tmp_path):
    mock = MockLLMClient(states=_STATE, tool_script=[("generate_image", {"prompt": "кіт", "filename": "c.png"})])
    core_bob = _core(tmp_path, mock, image=True, image_gen=_gen(), user="bob")
    core_bob.reply("намалюй", core_bob.start_session())
    assert (tmp_path / "files" / "bob" / "art" / "c.png").is_file()
    assert not (tmp_path / "files" / "alice" / "art" / "c.png").exists()

"""v0.23 LUMI-097 — contract: create-only, no personal data in the prompt, the cap, isolation, graceful
degradation, off-by-default, and the emotion contract over generate_image. Stub ImageGen; no paid calls.
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.emotion import EmotionState
from core.imagegen import ImageGenError
from core.llm import MockLLMClient
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 6, 17, 12, 0, tzinfo=UTC))
_CALM = {"reply": "ось", "emotion": "calm", "intensity": 0.5}


def _gen(data=b"\x89PNG-fake"):
    calls: list = []

    def gen(prompt: str, *, size: int = 768) -> bytes:
        calls.append(prompt)
        return data

    gen.calls = calls  # type: ignore[attr-defined]
    return gen


def _core(tmp_path, llm, *, image=True, image_gen=None, image_show="path,viewer,telegram", user="owner"):
    repo = JsonRepository(tmp_path / f"{user}.json")
    core = Core(
        llm=llm, repository=repo, canon="C", model="m", clock=_CLK, user_id=user,
        mood_enabled=False, closeness_enabled=False, thoughts_enabled=False,
        image_enabled=image, files_dir=tmp_path / "files", image_max_gen=2, image_show=image_show,
        image_signal_path=tmp_path / "image.txt", image_gen=image_gen, tool_max_steps=5,
    )
    return core, repo


# --- create-only (non-destructive) ----------------------------------------------------------------
def test_generate_is_create_only(tmp_path):
    mock = MockLLMClient(states=_CALM, tool_script=[
        ("generate_image", {"prompt": "a", "filename": "art.png"}),
        ("generate_image", {"prompt": "b", "filename": "art.png"}),  # same name → refused
    ])
    core, _ = _core(tmp_path, mock, image_gen=_gen(b"FIRST"))
    core.reply("намалюй двічі", core.start_session())
    assert mock.tool_calls[0][2].startswith("created") and "already exists" in mock.tool_calls[1][2]
    assert (tmp_path / "files" / "owner" / "art" / "art.png").read_bytes() == b"FIRST"  # untouched


# --- no personal/memory data in the prompt --------------------------------------------------------
def test_prompt_carries_only_the_models_request(tmp_path):
    gen = _gen()
    mock = MockLLMClient(states=_CALM, tool_script=[("generate_image", {"prompt": "кіт в окулярах"})])
    core, _ = _core(tmp_path, mock, image_gen=gen)
    core.reply("намалюй щось, мій давній друже", core.start_session())
    assert gen.calls == ["кіт в окулярах"]                 # exactly the model's prompt
    assert "друже" not in gen.calls[0] and "давній" not in gen.calls[0]  # the user's words did NOT leak


# --- cap + graceful degradation -------------------------------------------------------------------
def test_generation_cap(tmp_path):
    mock = MockLLMClient(states=_CALM,
                         tool_script=[("generate_image", {"prompt": p}) for p in ("a", "b", "c")])
    core, _ = _core(tmp_path, mock, image_gen=_gen())  # image_max_gen=2
    core.reply("намалюй три", core.start_session())
    results = [c[2] for c in mock.tool_calls]
    assert results[1].startswith("created") and "limit reached" in results[2]


def test_generation_error_degrades(tmp_path):
    def boom(prompt, *, size=768):
        raise ImageGenError("safety refusal")
    mock = MockLLMClient(states=_CALM, tool_script=[("generate_image", {"prompt": "заборонене"})])
    core, _ = _core(tmp_path, mock, image_gen=boom)
    state = core.reply("намалюй", core.start_session())
    assert isinstance(state, EmotionState)
    assert mock.tool_calls[0][2].startswith("error: image generation failed")


def test_traversal_filename_refused(tmp_path):
    mock = MockLLMClient(states=_CALM, tool_script=[("generate_image", {"prompt": "x", "filename": "../escape.png"})])
    core, _ = _core(tmp_path, mock, image_gen=_gen())
    state = core.reply("намалюй", core.start_session())
    assert isinstance(state, EmotionState) and "traversal" in mock.tool_calls[0][2]
    assert not (tmp_path / "escape.png").exists() and not (tmp_path / "files" / "escape.png").exists()


# --- per-user isolation ----------------------------------------------------------------------------
def test_generation_per_user_isolation(tmp_path):
    mock = MockLLMClient(states=_CALM, tool_script=[("generate_image", {"prompt": "кіт", "filename": "c.png"})])
    core_bob, _ = _core(tmp_path, mock, image_gen=_gen(), user="bob")
    core_bob.reply("намалюй", core_bob.start_session())
    assert (tmp_path / "files" / "bob" / "art" / "c.png").is_file()
    assert not (tmp_path / "files" / "alice" / "art" / "c.png").exists()  # only bob's sandbox


# --- off + the emotion contract -------------------------------------------------------------------
def test_off_no_generate_tool(tmp_path):
    mock = MockLLMClient(states=_CALM, tool_script=[("generate_image", {"prompt": "x"})])
    core, _ = _core(tmp_path, mock, image=False, image_gen=_gen())
    core.reply("намалюй", core.start_session())
    assert mock.tool_calls == []  # off → not offered


def test_emotion_contract_holds_with_generate(tmp_path):
    mock = MockLLMClient(states={"reply": "намалювала", "emotion": "joy", "intensity": 0.8},
                         tool_script=[("generate_image", {"prompt": "кіт"})])
    core, repo = _core(tmp_path, mock, image_gen=_gen())
    session = core.start_session()
    state = core.reply("намалюй кота", session)
    assert isinstance(state, EmotionState) and state.emotion.value == "joy" and 0 <= state.intensity <= 1
    assert [m.role for m in repo.load_messages(session.id)] == ["user", "lili"]  # both persisted

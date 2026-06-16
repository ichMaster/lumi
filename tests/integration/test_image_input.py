"""v0.22 LUMI-093 — shared-image input: the Core.reply images hook + the TUI /image command."""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.images import image_block
from core.llm import MockLLMClient
from state.local_store import JsonRepository
from tui.app import LumiApp

_CLK = fixed_clock(datetime(2026, 6, 16, 12, 0, tzinfo=UTC))
_STATE = {"reply": "бачу котика", "emotion": "tender", "intensity": 0.7}


def _core(tmp_path, llm, *, image=False) -> Core:
    return Core(
        llm=llm, repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.", model="m", clock=_CLK,
        mood_enabled=False, closeness_enabled=False, thoughts_enabled=False,
        image_enabled=image, vision_max=2,
    )


# --- the Core.reply images hook --------------------------------------------------------------------
def test_reply_attaches_shared_image_when_on(tmp_path):
    mock = MockLLMClient(states=_STATE)
    core = _core(tmp_path, mock, image=True)
    block = image_block(b"\x89PNG-fake")
    state = core.reply("що це?", core.start_session(), images=[block])
    assert state.emotion.value == "tender"
    assert mock.images_seen == [block]  # the image reached the model on the user message


def test_reply_drops_image_when_off(tmp_path):
    mock = MockLLMClient(states=_STATE)
    core = _core(tmp_path, mock, image=False)
    core.reply("що це?", core.start_session(), images=[image_block(b"x")])
    assert mock.images_seen == []  # off → not attached


def test_reply_image_capped_by_vision_max(tmp_path):
    mock = MockLLMClient(states=_STATE)
    core = _core(tmp_path, mock, image=True)  # vision_max=2
    core.reply("дивись", core.start_session(),
               images=[image_block(b"a"), image_block(b"b"), image_block(b"c")])
    assert len(mock.images_seen) == 2  # capped at vision_max


# --- the TUI /image command ------------------------------------------------------------------------
async def test_tui_image_command_shares_a_picture(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMI_IMAGE", "on")  # the TUI reads cfg.image at construction
    pic = tmp_path / "cat.png"
    pic.write_bytes(b"\x89PNG-fake")
    mock = MockLLMClient(states=_STATE)
    app = LumiApp(_core(tmp_path, mock, image=True))
    async with app.run_test() as pilot:
        app.query_one("#prompt").text = f"/image {pic} опиши"
        await pilot.press("enter")
        for _ in range(50):
            await pilot.pause()
            if len(app.transcript) >= 2:
                break
    assert any("🖼" in line and "cat.png" in line for line in app.transcript)  # the shared-image line
    assert len(mock.images_seen) == 1                                          # the picture reached the model


async def test_tui_image_command_off_shows_notice(tmp_path, monkeypatch):
    monkeypatch.delenv("LUMI_IMAGE", raising=False)  # off
    mock = MockLLMClient(states=_STATE)
    app = LumiApp(_core(tmp_path, mock, image=False))
    async with app.run_test() as pilot:
        app.query_one("#prompt").text = "/image /tmp/whatever.png"
        await pilot.press("enter")
        await pilot.pause()
    assert mock.images_seen == []  # vision off → nothing sent

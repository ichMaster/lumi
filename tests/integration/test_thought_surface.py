"""v0.33 LUMI-135 — TUI surfacing of a running directive: the status-line label + the gated chat-log
meta line (LUMI_THOUGHT_SURFACE). Pure helpers + a Textual pilot. No paid calls (mock model)."""
from __future__ import annotations

from core.agent import Core
from core.llm import MockLLMClient
from state.local_store import JsonRepository
from tui.app import LumiApp, thought_meta_line, thought_status_label


# --- the pure surfacing helpers -------------------------------------------------------------------
def test_thought_status_label():
    assert thought_status_label("brief") == "✦ %brief…"
    assert thought_status_label("brief", "news_read") == "✦ %brief · news_read…"  # directive + active tool


def test_thought_meta_line():
    assert thought_meta_line("catchup") == "✦ Лілі читає новини…"
    assert thought_meta_line("imagine") == "✦ Лілі малює…"
    assert thought_meta_line("zzz") == "✦ Лілі міркує…"  # unknown → generic fallback


# --- the gated chat-log meta line (through the real app) ------------------------------------------
def _core(tmp_path, llm):
    return Core(llm=llm, repository=JsonRepository(tmp_path / "store.json"),
                canon="Ти — Лілі.", model="m", mood_enabled=False, closeness_enabled=False)


async def _fire(pilot, app, text):
    app.query_one("#prompt").text = text
    await pilot.press("enter")
    for _ in range(20):
        await pilot.pause()


async def test_meta_line_appears_when_surface_on(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("тиха думка\nЕМОЦІЯ: calm")))
    async with app.run_test() as pilot:
        app._thought_surface = True
        await _fire(pilot, app, "%wonder")
        assert any("✦ Лілі" in line for line in app.transcript)  # the act is marked in the transcript


async def test_meta_line_absent_when_surface_off(tmp_path):
    app = LumiApp(_core(tmp_path, MockLLMClient("тиха думка\nЕМОЦІЯ: calm")))
    async with app.run_test() as pilot:
        app._thought_surface = False
        await _fire(pilot, app, "%wonder")
        assert not any("✦ Лілі" in line for line in app.transcript)  # off → no extra log line

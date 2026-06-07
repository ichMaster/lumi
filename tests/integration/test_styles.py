"""Answer styles — per-session overlays that shape how Лілі answers."""

from core.agent import Core
from core.config import load_config
from core.llm import MockLLMClient
from core.prompt import build_system_prompt
from core.styles import load_styles
from state.local_store import JsonRepository
from tui.app import ChatInput, LumiApp

STYLES = {"short": "Be brief.", "emotional": "Be warm."}


def _core(tmp_path, llm=None):
    return Core(
        llm=llm or MockLLMClient("ok"),
        repository=JsonRepository(tmp_path / "s.json"),
        canon="Ти — Лілі.",
        model="m",
        styles=STYLES,
    )


# --- loader ---------------------------------------------------------------
def test_load_styles_from_the_authored_file():
    styles = load_styles(load_config(load_env=False).styles_path)
    # The redesigned set spans the categories (length/explain/structure/register/…).
    assert {
        "short", "tldr", "detailed",
        "explain", "eli5", "example", "metaphor",
        "bullets", "steps", "compare", "practical",
        "formal", "casual", "emotional", "poetic",
        "socratic",
    } <= set(styles)
    assert "normal" not in styles  # the default carries no overlay
    # Category-header comments ("# ── Length ──") never leak into a style body.
    assert all("──" not in body for body in styles.values())
    # Each style includes a concrete length limit (sentences/words/lines).
    assert "речен" in styles["short"] or "слів" in styles["short"]


def test_load_styles_missing_file_is_empty(tmp_path):
    assert load_styles(tmp_path / "nope.md") == {}


# --- core -----------------------------------------------------------------
def test_default_style_is_normal_with_no_overlay(tmp_path):
    llm = MockLLMClient("ok")
    core = _core(tmp_path, llm)
    session = core.start_session()
    core.reply("привіт", session)
    assert core.style == "normal"
    assert "Be brief." not in llm.calls[-1]["system"]
    assert llm.calls[-1]["system"].startswith("Ти — Лілі.")


def test_set_style_injects_overlay_at_the_end_with_importance(tmp_path):
    llm = MockLLMClient("ok")
    core = _core(tmp_path, llm)
    session = core.start_session()
    assert core.set_style("short") is True
    core.reply("привіт", session)
    system = llm.calls[-1]["system"]
    assert "Be brief." in system
    assert system.index("Ти — Лілі.") < system.index("Be brief.")
    assert "ВАЖЛИВО" in system  # framed as a prioritized directive
    assert system.rstrip().endswith("Be brief.")  # the style is the LAST block


def test_set_style_unknown_is_rejected(tmp_path):
    core = _core(tmp_path)
    assert core.set_style("nope") is False
    assert core.style == "normal"


def test_style_names_are_normal_plus_overlays(tmp_path):
    assert _core(tmp_path).style_names() == ["normal", "emotional", "short"]


def test_style_is_per_session_and_resets(tmp_path):
    core = _core(tmp_path)
    core.start_session()
    core.set_style("short")
    assert core.style == "short"
    core.start_session()  # a new session resets the style
    assert core.style == "normal"


# --- prompt assembly ------------------------------------------------------
def test_build_system_prompt_places_style_last_with_importance():
    system = build_system_prompt(
        "CANON", summaries=["S"], facts=["F"], digest="D", style="STYLE-X"
    )
    # The style is the LAST block — after canon, summaries, facts, digest.
    for earlier in ("CANON", "S", "F", "D"):
        assert system.index(earlier) < system.index("STYLE-X")
    assert system.rstrip().endswith("STYLE-X")
    assert "ВАЖЛИВО" in system  # framed as a prioritized directive
    assert build_system_prompt("CANON") == "CANON"  # no style → canon verbatim


# --- TUI ------------------------------------------------------------------
async def test_style_command_lists_then_switches(tmp_path):
    core = _core(tmp_path)
    app = LumiApp(core)
    async with app.run_test() as pilot:
        app.query_one("#prompt", ChatInput).text = "/style"
        await pilot.press("enter")
        await pilot.pause()
        assert any("Styles:" in line for line in app.transcript)

        app.query_one("#prompt", ChatInput).text = "/style short"
        await pilot.press("enter")
        await pilot.pause()
        assert core.style == "short"
        assert any("Style → short" in line for line in app.transcript)
        assert "style: short" in app._status_text()


async def test_style_command_rejects_unknown(tmp_path):
    core = _core(tmp_path)
    app = LumiApp(core)
    async with app.run_test() as pilot:
        app.query_one("#prompt", ChatInput).text = "/style nope"
        await pilot.press("enter")
        await pilot.pause()
        assert any("Unknown style" in line for line in app.transcript)
        assert core.style == "normal"

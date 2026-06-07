"""Answer styles — per-session overlays that shape how Лілі answers."""

from core.agent import Core
from core.config import load_config
from core.llm import MockLLMClient
from core.prompt import build_system_prompt
from core.styles import load_meta_styles, load_styles
from state.local_store import JsonRepository
from tui.app import ChatInput, LumiApp

STYLES = {"short": "Be brief.", "emotional": "Be warm.", "formal": "Be formal."}
METAS = {"combo": ["short", "emotional"]}


def _core(tmp_path, llm=None):
    return Core(
        llm=llm or MockLLMClient("ok"),
        repository=JsonRepository(tmp_path / "s.json"),
        canon="Ти — Лілі.",
        model="m",
        styles=STYLES,
        meta_styles=METAS,
    )


# --- loader ---------------------------------------------------------------
def test_load_styles_from_the_authored_file():
    styles = load_styles(load_config(load_env=False).styles_path)
    # Base style names are Ukrainian, spanning the categories.
    assert {
        "коротко", "суть", "докладно",
        "поясни", "просто", "приклад", "метафора",
        "списком", "кроки", "порівняй", "практично",
        "офіційно", "невимушено", "емоційно", "поетично",
        "питанням",
    } <= set(styles)
    assert "normal" not in styles  # the default carries no overlay
    # Category-header comments ("# ── Довжина ──") never leak into a style body.
    assert all("──" not in body for body in styles.values())
    # Each style includes a concrete length limit (sentences/words/lines).
    assert "речен" in styles["коротко"] or "слів" in styles["коротко"]
    # Meta-styles are NOT base styles (their alias bodies are excluded).
    assert "стежка" not in styles and "іскра" not in styles


def test_load_meta_styles_from_the_authored_file():
    cfg = load_config(load_env=False)
    metas = load_meta_styles(cfg.styles_path)
    base = load_styles(cfg.styles_path)
    # Meta-style names are Лілі's, as adjectives.
    assert {
        "блискавична", "лагідна", "прискіплива", "завзята", "лірична", "допитлива"
    } <= set(metas)
    # Every meta expands to ≥2 real base styles.
    for name, members in metas.items():
        assert len(members) >= 2
        assert all(m in base for m in members), name


def test_load_styles_missing_file_is_empty(tmp_path):
    assert load_styles(tmp_path / "nope.md") == {}
    assert load_meta_styles(tmp_path / "nope.md") == {}


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


def test_system_prompt_has_the_reasoning_directive(tmp_path):
    # Every turn's system prompt asks the model to wrap reasoning in <think>…</think>
    # so it can be parsed out of the visible reply (Opus 4.8 thinking hygiene).
    llm = MockLLMClient("ok")
    core = _core(tmp_path, llm)
    core.reply("привіт", core.start_session())
    system = llm.calls[-1]["system"]
    assert "<think>" in system
    assert "лише те, що ти кажеш співрозмовнику" in system


def test_reply_strips_think_tags_and_stores_clean(tmp_path):
    # The model wraps reasoning in <think>…</think>; the stored/returned reply is
    # clean, and the reasoning surfaces as last_thinking (→ Thinking box).
    llm = MockLLMClient("<think>думаю, як відповісти.</think>Привіт, друже!")
    repo = JsonRepository(tmp_path / "s.json")
    core = Core(llm=llm, repository=repo, canon="Ти — Лілі.", model="m")
    session = core.start_session()
    out = core.reply("привіт", session)
    assert out.reply == "Привіт, друже!"  # returned reply (EmotionState) is clean
    assert core.last_thinking == "думаю, як відповісти."
    stored = [m.text for m in repo.load_messages(session.id) if m.role == "lili"]
    assert stored == ["Привіт, друже!"]  # the store never sees the reasoning


def test_multiple_styles_stack_in_order(tmp_path):
    llm = MockLLMClient("ok")
    core = _core(tmp_path, llm)
    session = core.start_session()
    assert core.set_style("short emotional") is True
    assert core.style == "short+emotional"
    core.reply("привіт", session)
    system = llm.calls[-1]["system"]
    # Both overlays present, in the given order.
    assert "Be brief." in system and "Be warm." in system
    assert system.index("Be brief.") < system.index("Be warm.")


def test_styles_accept_comma_and_plus_separators(tmp_path):
    core = _core(tmp_path)
    core.start_session()
    assert core.set_style("short, emotional") is True
    assert core.style == "short+emotional"
    assert core.set_style("emotional+short") is True
    assert core.style == "emotional+short"  # order follows the spec


def test_styles_are_all_or_nothing_on_unknown(tmp_path):
    core = _core(tmp_path)
    core.set_style("short")
    assert core.set_style("short nope") is False  # one bad name → reject all
    assert core.style == "short"  # unchanged


def test_normal_clears_active_styles(tmp_path):
    core = _core(tmp_path)
    core.set_style("short emotional")
    assert core.set_style("normal") is True
    assert core.style == "normal"


# --- meta-styles ----------------------------------------------------------
def test_meta_style_expands_to_several_base_styles(tmp_path):
    llm = MockLLMClient("ok")
    core = _core(tmp_path, llm)
    session = core.start_session()
    assert core.set_style("combo") is True  # combo = short + emotional
    assert core.style == "combo"  # display keeps the meta name
    core.reply("привіт", session)
    system = llm.calls[-1]["system"]
    assert "Be brief." in system and "Be warm." in system  # both base overlays
    assert system.index("Be brief.") < system.index("Be warm.")


def test_meta_and_base_styles_combine(tmp_path):
    core = _core(tmp_path)
    core.start_session()
    assert core.set_style("combo formal") is True
    assert core.style == "combo+formal"
    assert core._expand() == ["short", "emotional", "formal"]  # meta expanded + base


def test_meta_names_listed_separately(tmp_path):
    core = _core(tmp_path)
    assert core.meta_names() == ["combo"]
    assert core.base_names() == ["emotional", "formal", "short"]


def test_style_names_are_normal_plus_metas_plus_base(tmp_path):
    assert _core(tmp_path).style_names() == [
        "normal", "combo", "emotional", "formal", "short"
    ]


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
        assert any("Meta-styles:" in line for line in app.transcript)

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


async def test_status_shows_the_style_including_normal(tmp_path):
    core = _core(tmp_path)
    app = LumiApp(core)
    async with app.run_test():
        assert "style: normal" in app._status_text()  # always shown, even the default
        core.set_style("short")
        assert "style: short" in app._status_text()

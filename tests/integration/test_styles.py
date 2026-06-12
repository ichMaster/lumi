"""Answer styles — Лілі picks her own style each turn; /style is a soft recommendation."""

from core.agent import Core
from core.config import load_config
from core.llm import MockLLMClient
from core.prompt import build_system_prompt, split_style
from core.styles import load_meta_descriptions, load_meta_styles, load_styles
from state.local_store import JsonRepository
from tui.app import ChatInput, LumiApp

STYLES = {"short": "Be brief.", "emotional": "Be warm.", "formal": "Be formal."}
METAS = {"combo": ["short", "emotional"]}
META_DESC = {"combo": "Швидко й тепло, по суті."}


def _core(tmp_path, llm=None):
    return Core(
        llm=llm or MockLLMClient("ok"),
        repository=JsonRepository(tmp_path / "s.json"),
        canon="Ти — Лілі.",
        model="m",
        styles=STYLES,
        meta_styles=METAS,
        meta_descriptions=META_DESC,
    )


# --- loader (unchanged) ---------------------------------------------------
def test_load_styles_from_the_authored_file():
    styles = load_styles(load_config(load_env=False).styles_path)
    assert {
        "коротко", "суть", "докладно",
        "поясни", "просто", "приклад", "метафора",
        "списком", "кроки", "порівняй", "практично",
        "офіційно", "невимушено", "емоційно", "поетично",
        "питанням",
    } <= set(styles)
    assert "normal" not in styles  # the default carries no overlay
    assert all("──" not in body for body in styles.values())
    assert "речен" in styles["коротко"] or "слів" in styles["коротко"]


def test_load_meta_styles_from_the_authored_file():
    cfg = load_config(load_env=False)
    metas = load_meta_styles(cfg.styles_path)
    base = load_styles(cfg.styles_path)
    assert {
        "блискавична", "лагідна", "прискіплива", "завзята", "лірична", "допитлива"
    } <= set(metas)
    for name, members in metas.items():
        assert len(members) >= 2
        assert all(m in base for m in members), name


def test_load_meta_descriptions_from_the_authored_file():
    cfg = load_config(load_env=False)
    descs = load_meta_descriptions(cfg.styles_path)
    metas = load_meta_styles(cfg.styles_path)
    assert set(descs) == set(metas)  # every mega-style has a description
    assert "стисло" in descs["блискавична"].lower()  # the prose description, not the alias
    assert all(not d.startswith("=") for d in descs.values())


def test_load_styles_missing_file_is_empty(tmp_path):
    assert load_styles(tmp_path / "nope.md") == {}
    assert load_meta_styles(tmp_path / "nope.md") == {}
    assert load_meta_descriptions(tmp_path / "nope.md") == {}


# --- the <style> parser ---------------------------------------------------
def test_split_style_extracts_and_strips_the_tag():
    name, clean = split_style("Привіт, друже! <style>Лагідна</style>")
    assert name == "лагідна"  # lowercased
    assert clean == "Привіт, друже!"  # tag removed
    assert split_style("без тегу") == (None, "без тегу")
    assert split_style("текст <style/>")[1] == "текст"  # stray marker stripped


# --- auto-style: only the mega-styles (with descriptions) are offered ------
def test_every_turn_offers_the_mega_styles_with_descriptions(tmp_path):
    llm = MockLLMClient("ок")
    core = _core(tmp_path, llm)
    core.reply("привіт", core.start_session())
    system = llm.calls[-1]["system"]
    assert system.startswith("Ти — Лілі.")  # canon still first
    assert "combo: Швидко й тепло, по суті." in system  # the mega + its description
    assert not any(t in system for t in ("Be brief.", "Be warm.", "Be formal."))  # no base bodies
    assert "Базові стилі" not in system  # the long base list is gone
    assert "СТИЛЬ ВІДПОВІДІ" in system and "МЕГА" in system
    assert "<style>назва</style>" in system  # asked to declare her choice
    assert "ВАЖЛИВО" in system  # a prioritized directive, placed last


def test_no_styles_authored_means_no_palette(tmp_path):
    llm = MockLLMClient("ок")
    core = Core(llm=llm, repository=JsonRepository(tmp_path / "s.json"),
                canon="Ти — Лілі.", model="m")  # no styles/metas
    core.reply("привіт", core.start_session())
    assert "СТИЛЬ ВІДПОВІДІ" not in llm.calls[-1]["system"]


# --- her choice + who picked it -------------------------------------------
def test_she_declares_a_style_which_is_recorded_and_stripped(tmp_path):
    llm = MockLLMClient("Готово! <style>combo</style>")
    core = _core(tmp_path, llm)
    out = core.reply("привіт", core.start_session())
    assert out.reply == "Готово!"  # the tag never shows in the reply
    assert core.last_style == "combo"
    assert core.style == "combo (Лілі)"  # she chose it herself


def test_status_who_is_you_when_she_follows_the_recommendation(tmp_path):
    core = _core(tmp_path, MockLLMClient("ок <style>combo</style>"))
    s = core.start_session()
    assert core.set_style("combo") is True  # you recommend combo
    core.reply("привіт", s)
    assert core.style == "combo (ти)"  # she followed your recommendation


def test_status_who_is_lili_when_she_overrides_the_recommendation(tmp_path):
    core = _core(tmp_path, MockLLMClient("ок <style>formal</style>"))
    s = core.start_session()
    core.set_style("combo")  # you recommend combo…
    core.reply("привіт", s)
    assert core.style == "formal (Лілі)"  # …but she chose formal


def test_style_is_auto_before_her_first_reply(tmp_path):
    core = _core(tmp_path)
    core.start_session()
    assert core.style == "авто"
    core.set_style("combo")
    assert core.style == "авто · радиш: combo"  # her pick not made yet; rec shown


# --- /style as a recommendation -------------------------------------------
def test_set_style_sets_a_recommendation_in_the_prompt(tmp_path):
    llm = MockLLMClient("ок")
    core = _core(tmp_path, llm)
    s = core.start_session()
    assert core.set_style("combo") is True
    assert core.recommendation == "combo"
    core.reply("привіт", s)
    assert "Користувач радить: combo" in llm.calls[-1]["system"]


def test_set_style_auto_or_normal_clears_the_recommendation(tmp_path):
    core = _core(tmp_path)
    core.start_session()
    core.set_style("combo")
    assert core.set_style("auto") is True and core.recommendation == ""
    core.set_style("combo")
    assert core.set_style("normal") is True and core.recommendation == ""
    assert core.set_style("") is True and core.recommendation == ""


def test_set_style_unknown_is_rejected_and_keeps_the_recommendation(tmp_path):
    core = _core(tmp_path)
    core.start_session()
    core.set_style("combo")
    assert core.set_style("nope") is False
    assert core.recommendation == "combo"  # unchanged
    assert core.set_style("combo nope") is False  # all-or-nothing


def test_recommendation_dedupes_and_keeps_order(tmp_path):
    core = _core(tmp_path)
    core.start_session()
    assert core.set_style("formal, combo, formal") is True
    assert core.recommendation == "formal+combo"


def test_style_names_are_auto_plus_metas_plus_base(tmp_path):
    assert _core(tmp_path).style_names() == ["auto", "combo", "emotional", "formal", "short"]
    assert _core(tmp_path).meta_names() == ["combo"]
    assert _core(tmp_path).base_names() == ["emotional", "formal", "short"]


def test_recommendation_and_choice_reset_per_session(tmp_path):
    core = _core(tmp_path, MockLLMClient("ок <style>combo</style>"))
    s = core.start_session()
    core.set_style("combo")
    core.reply("привіт", s)
    assert core.style == "combo (ти)"
    core.start_session()  # a new session resets both
    assert core.recommendation == "" and core.last_style is None
    assert core.style == "авто"


# --- reasoning hygiene (unchanged) ----------------------------------------
def test_reply_strips_think_tags_and_stores_clean(tmp_path):
    llm = MockLLMClient("<think>думаю, як відповісти.</think>Привіт, друже!")
    repo = JsonRepository(tmp_path / "s.json")
    core = Core(llm=llm, repository=repo, canon="Ти — Лілі.", model="m")
    session = core.start_session()
    out = core.reply("привіт", session)
    assert out.reply == "Привіт, друже!"
    assert core.last_thinking == "думаю, як відповісти."
    stored = [m.text for m in repo.load_messages(session.id) if m.role == "lili"]
    assert stored == ["Привіт, друже!"]


# --- prompt assembly (v0.15 prefix/tail split — style stays last in the tail) ---
def test_build_system_prompt_places_style_last_with_importance():
    system, _ = build_system_prompt(
        "CANON", summaries=["S"], facts=["F"], digest="D", style="STYLE-X"
    )
    for earlier in ("CANON", "S", "F", "D"):
        assert system.index(earlier) < system.index("STYLE-X")
    assert system.rstrip().endswith("STYLE-X")  # style is the last block (the tail's tail)
    assert "ВАЖЛИВО" in system
    assert build_system_prompt("CANON") == ("CANON", "CANON")


# --- TUI ------------------------------------------------------------------
async def test_style_command_lists_and_recommends(tmp_path):
    core = _core(tmp_path)
    app = LumiApp(core)
    async with app.run_test() as pilot:
        app.query_one("#prompt", ChatInput).text = "/style"
        await pilot.press("enter")
        await pilot.pause()
        assert any("Лілі обирає стиль сама" in line for line in app.transcript)

        app.query_one("#prompt", ChatInput).text = "/style combo"
        await pilot.press("enter")
        await pilot.pause()
        assert core.recommendation == "combo"
        assert any("Рекомендація стилю → combo" in line for line in app.transcript)
        assert "combo" in app._status_text()  # shown in the status bar

        app.query_one("#prompt", ChatInput).text = "/style auto"
        await pilot.press("enter")
        await pilot.pause()
        assert core.recommendation == ""


async def test_style_command_rejects_unknown(tmp_path):
    core = _core(tmp_path)
    app = LumiApp(core)
    async with app.run_test() as pilot:
        app.query_one("#prompt", ChatInput).text = "/style nope"
        await pilot.press("enter")
        await pilot.pause()
        assert any("Unknown style" in line for line in app.transcript)
        assert core.recommendation == ""


async def test_status_shows_the_chosen_style_and_who(tmp_path):
    core = _core(tmp_path, MockLLMClient("ок <style>combo</style>"))
    app = LumiApp(core)
    async with app.run_test() as pilot:
        assert "style: авто" in app._status_text()  # before her first pick
        app.query_one("#prompt", ChatInput).text = "привіт"
        await pilot.press("enter")
        for _ in range(50):
            await pilot.pause()
            if len(app.transcript) >= 2:
                break
        assert "style: combo (Лілі)" in app._status_text()  # her self-chosen style + who

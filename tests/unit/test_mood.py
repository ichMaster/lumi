"""Unit tests for the v0.6 daily mood engine (LUMI-025) — mock model + fixed clock."""

import logging
from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from core.mood import load_natal, mood_request, split_resolution
from state.local_store import JsonRepository

_READING = (
    "День легкий і рухливий.\n\n"
    "РЕЗОЛЮЦІЯ:\n"
    "Хотітиметься спілкування й творчості; не хотітиметься рутини. Настрій жвавий."
)
_RESOLUTION = "Хотітиметься спілкування й творчості; не хотітиметься рутини. Настрій жвавий."

_DAY1 = fixed_clock(datetime(2026, 6, 7, 9, 0, tzinfo=UTC))


def _core(tmp_path, llm, clock=_DAY1, natal="Сонце 15° Риб", mood=True):
    return Core(
        llm=llm,
        repository=JsonRepository(tmp_path / "s.json"),
        canon="Ти — Лілі.",
        model="m",
        clock=clock,
        natal=natal,
        mood_enabled=mood,
    )


# --- pure helpers ---------------------------------------------------------
def test_mood_request_carries_the_natal_and_date():
    system, msgs = mood_request("Сонце 15° Риб", "2026-06-07")
    assert "астролог" in system.lower()
    assert "Сонце 15° Риб" in msgs[0]["content"] and "2026-06-07" in msgs[0]["content"]


def test_split_resolution_after_the_marker():
    assert split_resolution("текст\n\nРЕЗОЛЮЦІЯ:\nпідсумок дня") == "підсумок дня"


def test_split_resolution_falls_back_to_the_last_paragraph():
    assert split_resolution("перший абзац\n\nостанній абзац") == "останній абзац"


def test_split_resolution_handles_an_inline_marker():
    assert split_resolution("розклад\n\nРЕЗОЛЮЦІЯ: підсумок дня") == "підсумок дня"


def test_load_natal_skips_comments(tmp_path):
    f = tmp_path / "natal.md"
    f.write_text("# коментар\nСонце в Рибах\n", encoding="utf-8")
    assert load_natal(f) == "Сонце в Рибах"


def test_load_natal_missing_is_empty(tmp_path):
    assert load_natal(tmp_path / "nope.md") == ""


# --- engine ---------------------------------------------------------------
def test_mood_computed_once_per_local_day(tmp_path):
    llm = MockLLMClient(_READING)
    core = _core(tmp_path, llm)
    core._ensure_mood()
    core._ensure_mood()  # same day → cached, no second call
    assert core.mood == _RESOLUTION
    assert len(llm.calls) == 1


def test_mood_recomputes_across_local_midnight(tmp_path):
    llm = MockLLMClient(_READING)
    core = _core(tmp_path, llm, clock=fixed_clock(datetime(2026, 6, 7, 23, 0, tzinfo=UTC)))
    core._ensure_mood()
    assert len(llm.calls) == 1
    core._clock = fixed_clock(datetime(2026, 6, 8, 1, 0, tzinfo=UTC))  # next local day
    core._ensure_mood()
    assert len(llm.calls) == 2  # recomputed at the day boundary


def test_full_reading_logged_but_only_resolution_exposed(tmp_path, caplog):
    core = _core(tmp_path, MockLLMClient(_READING))
    with caplog.at_level(logging.INFO, logger="lumi.mood"):
        core._ensure_mood()
    assert core.mood == _RESOLUTION              # only the resolution is exposed
    assert "День легкий і рухливий" in caplog.text  # the full reading is in the log


def test_mood_failure_degrades_and_never_raises(tmp_path):
    def boom(system, messages, model):
        raise RuntimeError("mood call down")

    core = _core(tmp_path, MockLLMClient(boom))
    core._ensure_mood()  # must not raise
    assert core.mood is None


def test_mood_off_or_no_natal_makes_no_call(tmp_path):
    off = _core(tmp_path, MockLLMClient(_READING), mood=False)
    off._ensure_mood()
    assert off.mood is None and off._llm.calls == []

    no_natal = _core(tmp_path, MockLLMClient(_READING), natal="")
    no_natal._ensure_mood()
    assert no_natal.mood is None and no_natal._llm.calls == []


# --- injection (LUMI-026) -------------------------------------------------
def test_build_system_prompt_mood_is_a_prominent_block():
    from core.prompt import MOOD_HEADER, build_system_prompt

    assert "X-MOOD" not in build_system_prompt("CANON")[0]
    out, _ = build_system_prompt("CANON", mood="X-MOOD")
    assert "X-MOOD" in out and MOOD_HEADER in out  # prominent prioritized header


def test_only_the_resolution_is_injected_not_the_full_reading(tmp_path):
    from core.prompt import MOOD_HEADER

    llm = MockLLMClient(replies=_READING, states={"reply": "ок", "emotion": "calm", "intensity": 0.5})
    core = _core(tmp_path, llm)
    core.reply("привіт", core.start_session())
    system = core.last_prompt["system"]
    assert _RESOLUTION in system and MOOD_HEADER in system   # resolution as a prominent block
    assert "День легкий і рухливий" not in system            # full reading stays in the log only


def test_no_mood_means_no_block(tmp_path):
    from core.prompt import MOOD_HEADER

    llm = MockLLMClient(replies=_READING, states={"reply": "ок", "emotion": "calm", "intensity": 0.5})
    core = _core(tmp_path, llm, mood=False)  # mood off → no block
    core.reply("привіт", core.start_session())
    assert MOOD_HEADER not in core.last_prompt["system"]


def test_full_reading_is_persisted_to_the_mood_log_file(tmp_path):
    log = tmp_path / "mood.log"
    core = Core(
        llm=MockLLMClient(_READING),
        repository=JsonRepository(tmp_path / "s.json"),
        canon="Ти — Лілі.",
        model="m",
        clock=_DAY1,
        natal="Сонце 15° Риб",
        mood_log_path=log,
    )
    core._ensure_mood()
    assert log.is_file()
    text = log.read_text(encoding="utf-8")
    assert "День легкий і рухливий" in text and "2026-06-07" in text  # full reading, dated

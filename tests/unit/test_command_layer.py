"""Unit tests for the v2.2 command layer (LUMI-211) — every command, the confirm + turn protocols."""

from types import SimpleNamespace

import pytest

from core.agent import Core
from core.commands import (
    BIORHYTHM_OFF,
    CLEARED_LINE,
    COMMANDS,
    FORGET_QUESTION,
    JOURNAL_WRITE_TURN,
    MEMORY_EMPTY,
    MOOD_PENDING,
    CommandResult,
    run_command,
    tokens_line,
)
from core.llm import MockLLMClient
from core.repository import Session
from state.local_store import JsonRepository


def _core(tmp_path, **kw):
    return Core(
        llm=MockLLMClient("Привіт!"), repository=JsonRepository(tmp_path / "s.json"),
        canon="Ти — Лілі.", model="claude-haiku-4-5-20251001", **kw,
    )


def test_registry_has_the_fifteen_layer_commands():
    assert set(COMMANDS) == {
        "/memory", "/forget", "/prompt", "/latency", "/style", "/mood", "/model-set", "/model",
        "/biorhythm", "/closeness", "/thoughts", "/regen-summaries", "/recall", "/journal", "/theme",
    }


@pytest.mark.parametrize("line", ["/mode-set voice", "/web кава", "/new", "/image x.png", "/nope", "hello", ""])
def test_client_bound_and_unknown_lines_are_not_layer_commands(tmp_path, line):
    assert run_command(_core(tmp_path), None, line) is None


def test_a_no_arg_command_with_an_argument_falls_through(tmp_path):
    assert run_command(_core(tmp_path), None, "/mood please") is None  # exactly as the TUI matched it


def test_memory_empty_then_filled(tmp_path):
    core = _core(tmp_path)
    assert run_command(core, None, "/memory") == CommandResult(MEMORY_EMPTY)


def test_forget_asks_first_and_never_clears_unconfirmed(tmp_path):
    core = _core(tmp_path)
    session = core.start_session()
    core.reply("Мене звати Віталій.", session)
    asked = run_command(core, session, "/forget")
    assert asked.confirm == FORGET_QUESTION and asked.kind == "notice"  # (no side effect: the contract test)
    done = run_command(core, session, "/forget", confirmed=True)
    assert done == CommandResult(CLEARED_LINE, kind="ack")


def test_prompt_before_and_after_a_turn(tmp_path):
    core = _core(tmp_path)
    assert run_command(core, None, "/prompt").kind == "notice"
    session = core.start_session()
    core.reply("привіт", session)
    res = run_command(core, session, "/prompt")
    assert res.kind == "meta" and "[SYSTEM]" in res.text and "[MESSAGES]" in res.text


def test_latency_before_a_turn_is_a_notice(tmp_path):
    assert run_command(_core(tmp_path), None, "/latency").kind == "notice"


def test_style_list_set_and_unknown(tmp_path):
    core = _core(tmp_path)
    listed = run_command(core, None, "/style")
    assert listed.kind == "notice" and "Lili picks the style herself" in listed.text
    name = core.style_names()[0]
    assert run_command(core, None, f"/style {name}").kind == "ack"
    assert run_command(core, None, "/style no-such-style").kind == "error"


def test_mood_pending(tmp_path):
    assert run_command(_core(tmp_path), None, "/mood") == CommandResult(MOOD_PENDING)


def test_model_show_and_unknown_alias(tmp_path):
    core = _core(tmp_path)
    shown = run_command(core, None, "/model")
    assert shown.kind == "info" and "**Engine:**" in shown.text
    assert run_command(core, None, "/model no-such-alias-xyz").kind == "warning"


def test_model_set_without_profiles_warns(tmp_path):
    assert run_command(_core(tmp_path), None, "/model-set").kind == "warning"


def test_biorhythm_off(tmp_path):
    assert run_command(_core(tmp_path), None, "/biorhythm") == CommandResult(BIORHYTHM_OFF)


def test_closeness_and_thoughts(tmp_path):
    core = _core(tmp_path)
    assert run_command(core, None, "/closeness").text.startswith("**Closeness:**")
    assert run_command(core, None, "/thoughts").kind == "info"


def test_regen_summaries_is_a_notice(tmp_path):
    res = run_command(_core(tmp_path), None, "/regen-summaries")
    assert res.kind == "notice" and res.text.startswith("Regenerated ")


def test_recall_off(tmp_path):
    assert "Recall is off" in run_command(_core(tmp_path), None, "/recall кава").text


def test_recall_filters_and_session_exclusion():
    seen: dict = {}

    def recall_moments(query, *, exclude_session, before, after):
        seen.update(query=query, exclude=exclude_session, before=before, after=after)
        return ["— момент"]

    core = SimpleNamespace(recall_enabled=True, recall_moments=recall_moments)
    session = Session(id="s1", user_id="owner", started_at="2026-10-04T10:00")
    res = run_command(core, session, "/recall кава after:2026-01-01 before:2026-02-01")
    assert seen == {"query": "кава", "exclude": "s1", "before": "2026-02-01", "after": "2026-01-01"}
    assert res.text.startswith('**Recalling "кава" (from 2026-01-01, before 2026-02-01):**')
    run_command(core, session, "/recall кава !all")
    assert seen["exclude"] is None  # !all includes this conversation
    assert "Recall what?" in run_command(core, session, "/recall").text


def test_journal_off_is_a_toast(tmp_path):
    assert run_command(_core(tmp_path), None, "/journal").kind == "toast"


def test_journal_write_is_a_turn():
    core = SimpleNamespace(journal_enabled=True)
    res = run_command(core, None, "/journal write")
    assert res.turn == JOURNAL_WRITE_TURN


def test_journal_list_and_read():
    core = SimpleNamespace(
        journal_enabled=True, journal_list=lambda: "дати", journal_read=lambda date=None: f"read:{date}",
    )
    assert run_command(core, None, "/journal list").text == "дати"
    assert run_command(core, None, "/journal 2026-10-04").text == "read:2026-10-04"
    assert run_command(core, None, "/journal").text == "read:None"


def test_theme_show_set_auto_unknown():
    state = {"theme": None}

    def set_theme(name):
        if name in (None, "vigil"):
            state["theme"] = name
            return True
        return False

    core = SimpleNamespace(themes=["vigil"], theme=None, set_theme=set_theme)
    assert run_command(core, None, "/theme").text.startswith("**Face theme:** (the flat v0.7 pack)")
    assert run_command(core, None, "/theme vigil").text == "Face theme: **vigil**."
    assert run_command(core, None, "/theme auto").text.startswith("Face theme: **auto**")
    assert "Unknown theme" in run_command(core, None, "/theme nope").text


def test_tokens_line_formats_usage():
    stats = SimpleNamespace(input_tokens=1500, output_tokens=200, cache_read_tokens=1000,
                            cache_write_tokens=0, latency_ms=2300)
    assert tokens_line(stats) == "[TOKENS] in 1.5k · out 200 · cache 1.0k↩ · 2.3s"
    assert tokens_line(None) is None


# --- v4.8 LUMI-336: the commands answer in English; her content inside an answer stays hers ------------------------

_CYRILLIC = set("абвгґдеєжзиіїйклмнопрстуфхцчшщьюяАБВГҐДЕЄЖЗИІЇЙКЛМНОПРСТУФХЦЧШЩЬЮЯ")


def _english(text: str) -> bool:
    return not any(ch in _CYRILLIC for ch in text)


@pytest.mark.parametrize("line", [
    "/memory", "/mood", "/biorhythm", "/closeness", "/thoughts", "/style", "/style auto", "/style nope",
    "/model", "/model-set", "/recall", "/recall coffee", "/theme", "/theme auto", "/theme nope", "/forget",
    "/prompt", "/latency", "/journal",
])
def test_v48_every_commands_own_answer_is_english(tmp_path, line):
    # A fresh core holds none of her content (no mood, no thoughts, no memory), so the whole answer is the
    # command's own text — and it is English.
    res = run_command(_core(tmp_path), None, line)
    assert res is not None and _english(res.text), res.text
    assert res.confirm is None or _english(res.confirm)


def test_v48_the_mood_answer_wraps_her_resolution_in_english(tmp_path):
    core = SimpleNamespace(mood="Тиха зосередженість; хочеться глибини.")
    text = run_command(core, None, "/mood").text
    assert text.startswith("**Lili's mood today:**") and "Тиха зосередженість" in text  # her words, unchanged


def test_v48_biorhythm_and_cycle_lines_are_english_and_leave_her_formatters_alone():
    from core.biorhythm import Biorhythms, Cycle, format_biorhythms
    from core.commands import biorhythms_line, cycle_line
    from core.cycle import CyclePhase, format_cycle

    b = Biorhythms(physical=Cycle("physical", 1.0, "high"), emotional=Cycle("emotional", -0.61, "low"),
                   intellectual=Cycle("intellectual", 0.1, "rising"))
    assert biorhythms_line(b) == "physical +1.00 (high) · emotional -0.61 (low) · intellectual +0.10 (rising)"
    assert "фізичний" in format_biorhythms(b)  # her mood call and the journal stamp keep the Ukrainian line
    phase = CyclePhase(day=26, length=28, phase="ПМС", note="усе відчувається гостріше")
    assert cycle_line(phase).startswith("PMS (day 26/28) — everything feels sharper")
    assert format_cycle(phase).startswith("ПМС (день 26/28)")  # hers, unchanged


@pytest.mark.parametrize(("level", "name"), [(1, "Polite"), (2, "Friendly"), (3, "Warm"), (4, "Close"),
                                             (5, "Dearest")])
def test_v48_closeness_names_the_level_in_english(level, name):
    core = SimpleNamespace(closeness_status=lambda: (level, "Ім'я рівня"))
    assert run_command(core, None, "/closeness").text == f"**Closeness:** {name} (level {level} of 5)"


def test_v48_the_journal_request_she_is_handed_stays_hers(tmp_path):
    core = SimpleNamespace(journal_enabled=True)
    res = run_command(core, None, "/journal write")
    assert res.turn == JOURNAL_WRITE_TURN == "Запиши, будь ласка, підсумок сьогоднішнього дня у щоденник."

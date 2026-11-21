"""v5.1 LUMI-341 — a turn's tool calls and her picture in the instance's ledgers, over the mock model.

Each tool call → one ``tools.jsonl`` record joined to the turn by its ``turn_ts``; a generated picture → one
``image`` record in ``media-ledger.jsonl``. No result text, no prompt; no network, no paid call."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from core import ledgers, usage_origin
from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 11, 21, 12, 0, tzinfo=UTC))


def _core(tmp_path, *, tool_script, **kw):
    root = tmp_path / "files" / "owner"
    root.mkdir(parents=True, exist_ok=True)
    (root / "notes.md").write_text("вступ\nРозділ 4: оплата\nкінець\n", encoding="utf-8")
    return Core(
        llm=MockLLMClient(states={"reply": "ок", "emotion": "calm", "intensity": 0.5}, tool_script=tool_script),
        repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m", clock=_CLK,
        mood_enabled=False, closeness_enabled=False, thoughts_enabled=False,
        file_tool_enabled=True, files_dir=tmp_path / "files", file_read_lines=10,
        tools_ledger_path=tmp_path / ledgers.TOOLS_LEDGER, media_ledger_path=tmp_path / ledgers.MEDIA_LEDGER, **kw,
    )


def test_two_tool_calls_write_two_records_joined_to_the_turn(tmp_path):
    core = _core(tmp_path, tool_script=[("find_in_file", {"path": "notes.md", "query": "Розділ 4"}),
                                        ("read_file", {"path": "notes.md", "start_line": 2, "line_count": 1})])
    session = core.start_session()
    with usage_origin.usage_origin("turn", "window"):
        core.reply("прочитай розділ про оплату", session)
    recs = ledgers.read_records(tmp_path / ledgers.TOOLS_LEDGER)
    assert [r["tool"] for r in recs] == ["find_in_file", "read_file"]
    assert all(r["outcome"] == "ok" and r["ms"] >= 0 and r["result_chars"] > 0 for r in recs)
    assert all((r["origin"], r["channel"]) == ("turn", "window") for r in recs)
    assert recs[0]["input"] == "path=notes.md query=Розділ 4"
    # joined to the turn: the turn_id is the stamp both of its stored messages carry
    stored = core._repo.load_messages(session.id)  # noqa: SLF001 — the turn's own stored messages
    assert {m.ts for m in stored} == {recs[0]["turn_id"]} == {recs[1]["turn_id"]}
    text = (tmp_path / ledgers.TOOLS_LEDGER).read_text(encoding="utf-8")
    assert "оплата" not in text  # the result ("Розділ 4: оплата") is never recorded, only its size


def test_a_refused_and_an_unknown_tool_are_named_so(tmp_path):
    core = _core(tmp_path, tool_script=[("read_file", {"path": "../secret.md"}), ("no_such_tool", {})])
    core.reply("спробуй", core.start_session())
    outcomes = [r["outcome"] for r in ledgers.read_records(tmp_path / ledgers.TOOLS_LEDGER)]
    assert outcomes == ["refused:sandbox", "not_found"]


def test_a_tool_that_raises_is_recorded_before_the_loop_sees_it(tmp_path, monkeypatch):
    core = _core(tmp_path, tool_script=[("read_file", {"path": "notes.md"})])

    def boom(*_a, **_k):
        raise OSError("disk gone")

    monkeypatch.setattr(core, "_file_tool_args", lambda: ([{"name": "read_file"}], boom))
    try:
        core.reply("прочитай", core.start_session())
    except Exception:  # noqa: BLE001 — whatever the loop does with it, the record is there
        pass
    (rec,) = ledgers.read_records(tmp_path / ledgers.TOOLS_LEDGER)
    assert rec["outcome"] == "error:OSError"


def test_a_broken_tool_ledger_never_breaks_the_turn(tmp_path):
    (tmp_path / ledgers.TOOLS_LEDGER).mkdir()
    core = _core(tmp_path, tool_script=[("read_file", {"path": "notes.md", "start_line": 1, "line_count": 1})])
    state = core.reply("прочитай", core.start_session())
    assert state.reply == "ок"


def test_her_picture_writes_one_image_record_without_the_prompt(tmp_path):
    core = _core(tmp_path, tool_script=[("generate_image", {"prompt": "сіре небо і жовте вікно"})],
                 image_enabled=True, image_gen=lambda prompt, **kw: b"\x89PNG", image_model="gemini-2.5-flash-image")
    with usage_origin.usage_origin("turn", "telegram"):
        core.reply("намалюй вікно", core.start_session())
    (rec,) = ledgers.read_records(tmp_path / ledgers.MEDIA_LEDGER)
    assert (rec["service"], rec["model"], rec["units"]) == ("image", "gemini-2.5-flash-image", {"images": 1})
    assert (rec["origin"], rec["channel"], rec["outcome"]) == ("turn", "telegram", "ok")
    assert "небо" not in json.dumps(rec, ensure_ascii=False)
    tool = ledgers.read_records(tmp_path / ledgers.TOOLS_LEDGER)[0]
    assert tool["tool"] == "generate_image" and tool["outcome"] == "ok"


def test_a_web_lookup_writes_one_search_record(tmp_path):
    def search(query, *, today):
        return "комета видно в грудні"

    core = _core(tmp_path, tool_script=[("web_lookup", {"query": "comet december"})],
                 web_lookup_enabled=True, web_search=search, web_lookup_model="gemini-2.5-flash")
    core.reply("що з кометою?", core.start_session())
    (rec,) = ledgers.read_records(tmp_path / ledgers.MEDIA_LEDGER)
    assert (rec["service"], rec["units"], rec["outcome"]) == ("search", {"requests": 1}, "ok")
    assert "comet" not in json.dumps(rec)


def test_without_ledger_paths_nothing_is_written(tmp_path):
    core = Core(llm=MockLLMClient(states={"reply": "ок", "emotion": "calm", "intensity": 0.5},
                                  tool_script=[("list_files", {})]),
                repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m", clock=_CLK,
                mood_enabled=False, closeness_enabled=False, thoughts_enabled=False,
                file_tool_enabled=True, files_dir=tmp_path / "files")
    (tmp_path / "files" / "owner").mkdir(parents=True)
    core.reply("що є?", core.start_session())
    assert not (tmp_path / ledgers.TOOLS_LEDGER).exists()

"""Contract: the v2.2 command layer (LUMI-211) — one result shape, two client protocols, no UI imports.

Every client (the TUI now, the server/CLI and the pywebview client later) depends on exactly this:
``run_command(core, session, line, *, confirmed=False) -> CommandResult | None`` with a fixed set of
semantic kinds; ``confirm`` means "nothing happened yet"; ``turn`` means "run this as a turn".
"""

import ast
import dataclasses
from pathlib import Path

from core.agent import Core
from core.commands import KINDS, CommandResult, run_command
from core.llm import MockLLMClient
from state.local_store import JsonRepository

_SRC = Path(__file__).resolve().parents[2] / "core" / "commands.py"


def test_result_shape_is_pinned():
    fields = [(f.name, f.default) for f in dataclasses.fields(CommandResult)]
    assert fields == [("text", dataclasses.MISSING), ("kind", "info"), ("confirm", None), ("turn", None)]
    assert KINDS == ("info", "notice", "ack", "meta", "warning", "error", "toast")


def test_the_layer_imports_no_interface_code():
    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    modules = {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    modules |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not modules & {"tui", "rich", "textual", "server", "cli", "fastapi"}  # the core stays interface-free


def test_every_result_has_a_known_kind(tmp_path):
    core = Core(llm=MockLLMClient("ok"), repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m")
    for line in ("/memory", "/forget", "/prompt", "/latency", "/style", "/mood", "/model", "/model-set",
                 "/biorhythm", "/closeness", "/thoughts", "/regen-summaries", "/recall x", "/journal", "/theme"):
        res = run_command(core, None, line)
        assert isinstance(res, CommandResult) and res.kind in KINDS, line


def test_confirm_means_nothing_happened(tmp_path):
    core = Core(llm=MockLLMClient("ok"), repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m")
    session = core.start_session()
    core.reply("Мене звати Віталій.", session)
    before = core.view_memory()
    asked = run_command(core, session, "/forget")
    assert asked.confirm is not None
    assert core.view_memory() == before  # unconfirmed → no side effect

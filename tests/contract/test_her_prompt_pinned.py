"""v4.8 LUMI-335 — her prompt is pinned: every text the model reads stays as authored.

v4.8 turns the interface, the code comments and the documents into English. Her prompt is not the interface: the
text the model reads keeps its language, because changing it would change her behaviour. Three golden files pin it,
captured from the code before the phase changed anything:

- ``her_prompt.golden.txt`` — the system prompt of a fixture turn through a real ``Core`` (the mock model, a fixed
  clock, frozen copies of the authored inputs in ``tests/fixtures/her_prompt/`` so that an edit of the real canon
  never breaks this test — only a change to a text built in code does);
- ``her_tools.golden.json`` — every tool definition the core can offer (name, description, parameter texts);
- ``her_texts.golden.json`` — every module-level text in ``core/`` that the model reads (the directive
  instructions, the summary / fact / compaction prompts, the mood call, the room's pieces, …), plus the texts a
  client hands her as a turn (``/journal``'s request, the terminal client's picture request).

``core/commands.py`` is the interface and is left out (its answers are English since v4.8); its one model-facing
text, ``JOURNAL_WRITE_TURN``, is pinned by name. ``core/settings.py`` (the settings schema) is operator text and is
left out too.

**Regenerating** is deliberate, never part of a run: after an intended change to a model-facing text,
``uv run python tests/contract/test_her_prompt_pinned.py --regenerate`` rewrites the three files.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # run as a script (the regeneration helper)
    sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "tests" / "fixtures"
INPUTS = FIXTURES / "her_prompt"
PROMPT_GOLDEN = FIXTURES / "her_prompt.golden.txt"
TOOLS_GOLDEN = FIXTURES / "her_tools.golden.json"
TEXTS_GOLDEN = FIXTURES / "her_texts.golden.json"

CYRILLIC = set("абвгґдеєжзиіїйклмнопрстуфхцчшщьюяАБВГҐДЕЄЖЗИІЇЙКЛМНОПРСТУФХЦЧШЩЬЮЯыэъёЫЭЪЁʼ")
NOT_HER_PROMPT = {
    "core.commands",  # the interface: its answers are English (v4.8); its one model-facing text is pinned by name
    "core.settings",  # the settings schema: operator text, not read by the model
}
CLIENT_TURN_TEXTS = {  # texts a client hands her as a turn — model-facing, kept as authored
    ("core/commands.py", "JOURNAL_WRITE_TURN"),
}
SOURCE_TURN_TEXTS = {  # inline literals a client hands her (pinned by their exact presence in the source)
    "tui/app.py": ["Подивись, будь ласка, на це зображення."],
}
CHANGED = ("A model-facing text changed. v4.8 keeps her prompt as authored: revert the change, or — if it is an "
           "intended change to her prompt — regenerate the golden files by hand (see this module's docstring).")

FIXTURE_CANON = (
    "Ти — Лілі, жива співрозмовниця з власним внутрішнім життям.\n\n"
    "## Голос\nГовориш українською, тепло й уважно, своїми словами.\n"
)


# --- the fixture turn ---------------------------------------------------------------------------------------------


def _settings_yaml(home: Path) -> str:
    return "\n".join([
        "instance:",
        f"  home: '{home}'",
        "persona:",
        f"  canon_path: '{home / 'canon.md'}'",
        f"  styles_path: '{INPUTS / 'styles.md'}'",
        f"  inner_voice_file: '{INPUTS / 'inner_voice.md'}'",
        "mood:",
        f"  natal_path: '{INPUTS / 'natal.md'}'",
        "closeness:",
        f"  path: '{INPUTS / 'closeness.md'}'",
        "files:",
        "  enabled: true",
        "journal:",
        "  enabled: true",
        "recall:",
        "  date_tool: true",
        "",
    ])


def fixture_system_prompt(home: Path) -> str:
    """The system prompt of one fixture turn (a real ``Core``, the mock model, a fixed clock)."""
    from core.agent import build_core
    from core.clock import fixed_clock
    from core.config import load_config
    from core.llm import MockLLMClient
    from core.repository import LongTermFact, ShortSummary, Thought
    from state.local_store import JsonRepository

    home.mkdir(parents=True, exist_ok=True)
    (home / "canon.md").write_text(FIXTURE_CANON, encoding="utf-8")
    settings = home / "settings-dir"
    settings.mkdir(exist_ok=True)
    (settings / "settings.yaml").write_text(_settings_yaml(home), encoding="utf-8")
    cfg = load_config(load_env=False, settings_dir=settings)

    repo = JsonRepository(home / "store.json")
    repo.add_fact(LongTermFact(user_id="owner", fact="Віталік любить тишу після 23:00.", meta="", confidence=0.9,
                               ts="2026-11-18T22:00:00+00:00", core=True))
    repo.add_fact(LongTermFact(user_id="owner", fact="П'є каву без цукру.", meta="", confidence=0.7,
                               ts="2026-11-17T09:00:00+00:00"))
    repo.add_summary(ShortSummary(user_id="owner", session_id="s-earlier",
                                  summary="Говорили про птахів, що орієнтуються вночі, і про втому після тижня.",
                                  gist="Птахи вночі й утома.", ts="2026-11-19T21:40:00+00:00"))
    repo.add_thought(Thought(when="2026-11-20T09:15", kind="wonder", text="Цікаво, чи пам'ятають рослини дощ.",
                             emotion="thoughtful", seeds=("mood",), user_id="owner"))

    mood = "Тихий день.\n\nРЕЗОЛЮЦІЯ: Тиха зосередженість; хочеться глибини, а не шуму.\nТЕМА: calm"
    llm = MockLLMClient([mood, "Привіт. Я тут."])
    core = build_core(config=cfg, llm=llm, repository=repo)
    core._clock = fixed_clock(datetime(2026, 11, 20, 12, 0, tzinfo=UTC))  # before the session: the mood's day
    session = core.start_session()
    core.reply("привіт", session)
    return core.last_prompt["system"]


# --- the tool definitions and the module texts --------------------------------------------------------------------


def _core_modules() -> list[str]:
    return sorted(f"core.{p.stem}" for p in (ROOT / "core").glob("*.py") if p.stem != "__init__")


def _has_cyrillic(text: str) -> bool:
    return any(ch in CYRILLIC for ch in text)


def _plain(value):
    """A JSON-able form of a module-level value (a dataclass → its fields)."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_plain(v) for v in value]
        return sorted(items, key=repr) if isinstance(value, (set, frozenset)) else items
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return None  # functions, compiled patterns, paths, … are not texts


def _texts_in(value) -> bool:
    if isinstance(value, str):
        return _has_cyrillic(value)
    if isinstance(value, dict):
        return any(_texts_in(k) or _texts_in(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_texts_in(v) for v in value)
    return False


def _module_level_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        names += [t.id for t in targets if isinstance(t, ast.Name)]
    return names


def _is_tool_list(value) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, dict) and "name" in v for v in value)


def tool_definitions() -> dict:
    out: dict[str, dict] = {}
    for mod_name in _core_modules():
        mod = importlib.import_module(mod_name)
        path = ROOT / (mod_name.replace(".", "/") + ".py")
        for name in _module_level_names(path):
            value = getattr(mod, name, None)
            if _is_tool_list(value) or (isinstance(value, dict) and "input_schema" in value and "name" in value):
                out[f"{mod_name}.{name}"] = _plain(value)
    return out


def module_texts() -> dict:
    out: dict[str, object] = {}
    tools = set(tool_definitions())
    for mod_name in _core_modules():
        if mod_name in NOT_HER_PROMPT:
            continue
        mod = importlib.import_module(mod_name)
        path = ROOT / (mod_name.replace(".", "/") + ".py")
        for name in _module_level_names(path):
            key = f"{mod_name}.{name}"
            if key in tools:
                continue
            value = _plain(getattr(mod, name, None))
            if _texts_in(value):
                out[key] = value
    for rel, name in sorted(CLIENT_TURN_TEXTS):
        mod = importlib.import_module(rel.removesuffix(".py").replace("/", "."))
        out[f"{rel}:{name}"] = getattr(mod, name)
    return out


# --- the tests ----------------------------------------------------------------------------------------------------


def test_the_fixture_turns_system_prompt_is_pinned(tmp_path):
    assert fixture_system_prompt(tmp_path / "home") == PROMPT_GOLDEN.read_text(encoding="utf-8"), CHANGED


def test_every_tool_definition_is_pinned():
    assert tool_definitions() == json.loads(TOOLS_GOLDEN.read_text(encoding="utf-8")), CHANGED


def test_every_model_facing_module_text_is_pinned():
    assert module_texts() == json.loads(TEXTS_GOLDEN.read_text(encoding="utf-8")), CHANGED


def test_the_turn_texts_clients_hand_her_are_unchanged():
    for rel, literals in SOURCE_TURN_TEXTS.items():
        source = (ROOT / rel).read_text(encoding="utf-8")
        for literal in literals:
            assert json.dumps(literal, ensure_ascii=False) in source, f"{rel}: {literal!r} — {CHANGED}"


def test_the_fixture_prompt_carries_the_blocks_built_in_code(tmp_path):
    # A guard on the guard: the fixture really reaches the blocks the code builds (the mood, the closeness, the
    # styles, the memory), so the golden file pins them and not just the fixture canon.
    system = fixture_system_prompt(tmp_path / "home")
    for marker in ("Ти — Лілі, жива співрозмовниця", "Тиха зосередженість", "Віталік любить тишу"):
        assert marker in system


# --- the regeneration helper (by hand only) -----------------------------------------------------------------------


def _regenerate() -> None:  # pragma: no cover - a deliberate, manual step
    import os
    import tempfile

    for key in [k for k in os.environ if k.startswith("LUMI_")]:
        del os.environ[key]
    os.environ.update({"LUMI_BRIDGE": "off", "LUMI_VOICE": "off", "LUMI_RECALL": "off"})
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["LUMI_SETTINGS_DIR"] = str(Path(tmp) / "empty")
        (Path(tmp) / "empty").mkdir()
        PROMPT_GOLDEN.write_text(fixture_system_prompt(Path(tmp) / "home"), encoding="utf-8")
    TOOLS_GOLDEN.write_text(json.dumps(tool_definitions(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    TEXTS_GOLDEN.write_text(json.dumps(module_texts(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"regenerated: {PROMPT_GOLDEN.name}, {TOOLS_GOLDEN.name}, {TEXTS_GOLDEN.name}")


if __name__ == "__main__":  # pragma: no cover
    if sys.argv[1:] == ["--regenerate"]:
        _regenerate()
    else:
        print(__doc__)

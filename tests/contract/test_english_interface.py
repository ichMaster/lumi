"""v4.8 LUMI-338 — the guard: the interface and the code comments are English.

The owner's rule (2026-11-20): the documentation, the interface and the code comments are English only; her words and
her prompt stay Ukrainian (LUMI-335 pins her prompt). This module keeps the English side from drifting back:

- **The interface's string literals** — in the modules that talk to a person (the command layer, the terminal client,
  the CLI, the viewer, the server, the Telegram daemons, the window's shell, the admin panel) no literal holds Cyrillic,
  apart from :data:`ALLOWED_LITERALS`: an accepted Ukrainian answer, the texts a client hands her as a turn, and a
  line an external contract writes.
- **The window's and the panel's pages** — no Cyrillic in their JS or HTML; the window's string table holds Cyrillic
  only as the keys of her style palette (her words, mapped to English names).
- **Every comment and docstring in the code** — Cyrillic only inside a quote (``«…»``, ``"…"``, ``'…'``, a backtick
  span) or as her name «Лілі»: a comment may quote her text or a pattern the code matches, but it is written in English.
"""

from __future__ import annotations

import ast
import io
import json
import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CYRILLIC = re.compile("[А-Яа-яЄєІіЇїҐґЁёЫыЭэЪъʼ]")
CODE_ROOTS = ("core", "server", "tui", "cli", "desktop", "telegram", "voice", "viewer", "admin", "state", "scripts")
INTERFACE = ("core/commands.py", "tui", "cli", "viewer", "server", "telegram", "desktop", "admin")
ALLOWED_LITERALS = {
    "т",  # the Ukrainian yes, still accepted (the terminal client's confirm key, the CLI's answer)
    "так",
    "Подивись, будь ласка, на це зображення.",  # the terminal client's picture request — a turn she is handed
    "Запиши, будь ласка, підсумок сьогоднішнього дня у щоденник.",  # /journal write — a turn she is handed
    "Лілі is answering the room — try again",  # docs/agora-space-api.json, the contract shared with matrix-agora
}
# A quote of her text or of a pattern: «…» (may span lines), “…”, "…" and '…' (never across a line), a backtick span.
# A single quote opens or closes a quote only outside a word, so the apostrophes of "turn's … doesn't" are not one
# (code review #1).
QUOTED = re.compile(r"«[^»]*»|“[^”\n]*”|\"[^\"\n]*\"|`+[^`]*`+|(?<!\w)'[^'\n]*'(?!\w)")
RULE = ("the interface and the code comments are English (v4.8): write it in English; Ukrainian only inside a quote "
        "of her text or of a pattern the code matches, or as her name «Лілі»")


def _py_files(*roots: str) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        path = ROOT / root
        out += [path] if path.is_file() else sorted(p for p in path.rglob("*.py") if "__pycache__" not in p.parts)
    return out


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """The ids of the Constant nodes that are docstrings (module, class, function)."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                ids.add(id(first.value))
    return ids


def _prose_left(text: str) -> str | None:
    """The first Cyrillic word left once quotes and her name are taken out, or ``None``."""
    rest = QUOTED.sub(" ", text).replace("Лілі", " ")
    m = CYRILLIC.search(rest)
    if m is None:
        return None
    start = max(0, m.start() - 20)
    return rest[start:m.start() + 30].replace("\n", " ")


def interface_literal_violations() -> list[str]:
    found: list[str] = []
    for path in _py_files(*INTERFACE):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs \
                    and CYRILLIC.search(node.value) and node.value not in ALLOWED_LITERALS:
                found.append(f"{path.relative_to(ROOT)}:{node.lineno}: {node.value[:60]!r}")
    return found


def comment_and_docstring_violations() -> list[str]:
    found: list[str] = []
    for path in _py_files(*CODE_ROOTS):
        source = path.read_text(encoding="utf-8")
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT and (left := _prose_left(tok.string)):
                found.append(f"{path.relative_to(ROOT)}:{tok.start[0]}: # …{left}…")
        tree = ast.parse(source)
        docs = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if id(node) in docs and (left := _prose_left(node.value)):
                found.append(f"{path.relative_to(ROOT)}:{node.lineno}: docstring …{left}…")
    return found


def page_violations() -> list[str]:
    found: list[str] = []
    for folder in ("desktop/ui", "admin/static"):
        for path in sorted((ROOT / folder).glob("*")):
            if path.suffix in (".js", ".html", ".css"):
                for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if CYRILLIC.search(line):
                        found.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:60]}")
    strings = json.loads((ROOT / "desktop/ui/strings.en.json").read_text(encoding="utf-8"))

    def walk(value, where: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if CYRILLIC.search(key) and where != "styles":  # her palette's keys are her words
                    found.append(f"desktop/ui/strings.en.json: key {key!r}")
                walk(item, key if where == "" else where)
        elif isinstance(value, list):
            for item in value:
                walk(item, where)
        elif isinstance(value, str) and CYRILLIC.search(value):
            found.append(f"desktop/ui/strings.en.json: {value[:60]!r}")

    walk(strings, "")
    return found


def test_the_interface_literals_are_english():
    assert interface_literal_violations() == [], RULE


def test_the_window_and_the_panel_pages_are_english():
    assert page_violations() == [], RULE


def test_every_comment_and_docstring_is_english():
    assert comment_and_docstring_violations() == [], RULE


def test_the_guard_catches_a_ukrainian_word():
    # A guard on the guard: Ukrainian prose is caught; a quote of it, a backtick span and her name are not.
    assert _prose_left("# the mood is cut off before its close — тут обрізано") is not None
    assert _prose_left("# before its `РЕЗОЛЮЦІЯ` close, e.g. «Зараз 21:40»; Лілі's turn") is None


def test_english_apostrophes_never_hide_ukrainian_prose():
    # Code review #1: an apostrophe pair ("turn's … doesn't") read as a quote let Ukrainian prose through; so did a
    # stray double quote pairing with one on another line.
    assert _prose_left("# the turn's tail — тут обрізано — doesn't matter") is not None
    assert _prose_left("# Lili's mood is тихий, isn't it") is not None
    assert _prose_left('"""A 12" frame.\nтут проза\nthen "x"."""') is not None
    for quoted in ("# the word 'тихо' here", '# the word "тихо" here', "# «тихо, тихо»", "# the `тихо` key",
                   "# a quote «that spans\n# тихо two lines»"):
        assert _prose_left(quoted) is None, quoted


# --- v4.8 LUMI-339: the documentation is English -------------------------------------------------------------------

DOC_ROOTS = ("specification", "docs", "design")
UKRAINIAN_DOC = re.compile(r"(\.uk|_uk|_UK)\.md$")


def ukrainian_documents() -> list[str]:
    found = [str(p.relative_to(ROOT)) for root in DOC_ROOTS for p in (ROOT / root).rglob("*.md")
             if UKRAINIAN_DOC.search(p.name) or "ukrainian" in p.relative_to(ROOT).parts]
    found += [p.name for p in ROOT.glob("*.md") if UKRAINIAN_DOC.search(p.name)]
    return sorted(found)


def test_no_ukrainian_document_is_left():
    assert ukrainian_documents() == [], ("the documentation is English (v4.8): translate the document, or remove it "
                                         "when an English twin covers it")

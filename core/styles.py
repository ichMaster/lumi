"""Answer styles — named system-prompt overlays that shape *how* Лілі answers.

A style colors the **form** of a reply — length, structure, expressiveness
(short / explain / emotional / …) — not what she knows. It's injected into the
system prompt like the canon's other overlays. Styles are authored in
``core/styles.md`` (editable). The active style is **per-session** (resets to
``normal`` each session).

A **meta-style** is a preset whose body is an alias line ``= a, b, c`` — choosing
it selects several base styles at once (e.g. ``teacher = explain, eli5, example``).
"""

from __future__ import annotations

import re
from pathlib import Path

# The default style — Лілі's plain answers, with no overlay.
DEFAULT_STYLE = "normal"


def _sections(path: str | Path) -> dict[str, str]:
    """Parse ``## name`` sections into ``{name: body}`` (``normal``/empty dropped)."""
    p = Path(path)
    if not p.is_file():
        return {}
    sections: dict[str, str] = {}
    name: str | None = None
    buf: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            if name is not None:
                sections[name] = "\n".join(buf).strip()
            name = line[3:].strip().lower()
            buf = []
        elif line.startswith("#"):
            continue  # comment / category header
        elif name is not None:
            buf.append(line)
    if name is not None:
        sections[name] = "\n".join(buf).strip()
    return {k: v for k, v in sections.items() if v and k != DEFAULT_STYLE}


def load_styles(path: str | Path) -> dict[str, str]:
    """Base styles ``{name: overlay_text}`` — prose sections only (no meta aliases).

    A missing file yields ``{}`` (styles are optional); only ``normal`` is then
    available.
    """
    return {n: b for n, b in _sections(path).items() if not b.startswith("=")}


def load_meta_styles(path: str | Path) -> dict[str, list[str]]:
    """Meta-styles ``{name: [base style names]}`` — the ``= a, b, c`` alias sections."""
    metas: dict[str, list[str]] = {}
    for name, body in _sections(path).items():
        if body.startswith("="):
            metas[name] = [n for n in re.split(r"[\s,+]+", body[1:].strip()) if n]
    return metas

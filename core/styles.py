"""Answer styles — named system-prompt overlays that shape *how* Лілі answers.

A style colors the **form** of a reply — length, structure, expressiveness
(short / explain / emotional / …) — not what she knows. It's injected into the
system prompt like the canon's other overlays. Styles are authored in
``core/styles.md`` (editable). The active style is **per-session** (resets to
``normal`` each session).
"""

from __future__ import annotations

from pathlib import Path

# The default style — Лілі's plain answers, with no overlay.
DEFAULT_STYLE = "normal"


def load_styles(path: str | Path) -> dict[str, str]:
    """Parse ``styles.md`` into ``{name: overlay_text}``.

    Each ``## <name>`` heading starts a style; the lines until the next heading
    are its overlay. Single-``#`` lines are comments. ``normal`` (and any empty
    body) is dropped — it carries no overlay. A missing file yields ``{}`` (styles
    are optional); only ``normal`` is then available.
    """
    p = Path(path)
    if not p.is_file():
        return {}
    styles: dict[str, str] = {}
    name: str | None = None
    buf: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            if name is not None:
                styles[name] = "\n".join(buf).strip()
            name = line[3:].strip().lower()
            buf = []
        elif line.startswith("#"):
            continue  # comment / title
        elif name is not None:
            buf.append(line)
    if name is not None:
        styles[name] = "\n".join(buf).strip()
    return {k: v for k, v in styles.items() if v and k != DEFAULT_STYLE}

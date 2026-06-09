"""Face-theme manifest + folder discovery (v0.11) — pure, testable, no GUI.

A **theme** is a wardrobe pack under `faces/<theme>/<emotion>/…` (with a required per-theme
`calm/`). The optional manifest `faces/themes.md` gives each theme a **one-line description** (the
text the v0.6 mood chooses from) and names the **default theme**. Theme folders are also
**auto-discovered** (a `faces/` subdir that has a `calm/` folder). A missing manifest degrades to
the default/flat v0.7 behavior (no themes).

Manifest format (editable, like the canon/styles):

    default: cozy          # the fallback theme (when the mood is off/unknown)

    ## cozy
    Warm, soft, intimate lighting.

    ## 3am
    Rooftop loneliness at 3AM — misty-eyed, headphones on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_THEME_RE = re.compile(r"^##\s*(.+?)\s*$")
_DEFAULT_RE = re.compile(r"^default\s*[:=]\s*(.+?)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Themes:
    """The resolved theme set: ``{name: one-line description}`` + the ``default`` theme name."""

    descriptions: dict[str, str] = field(default_factory=dict)
    default: str | None = None

    @property
    def names(self) -> list[str]:
        return sorted(self.descriptions)


def discover_themes(faces_dir: str | Path) -> list[str]:
    """Theme folders under ``faces/`` — a subdir that contains a ``calm/`` folder (sorted)."""
    faces = Path(faces_dir)
    if not faces.is_dir():
        return []
    return sorted(
        child.name
        for child in faces.iterdir()
        if child.is_dir() and (child / "calm").is_dir()
    )


def _parse_manifest(path: Path) -> tuple[dict[str, str], str | None]:
    """Parse ``themes.md`` → (``{name: description}``, default-theme-name). Missing file → ({}, None)."""
    if not path.is_file():
        return {}, None
    descs: dict[str, str] = {}
    default: str | None = None
    name: str | None = None
    buf: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        header = _THEME_RE.match(line)
        if header:
            if name is not None:
                descs[name] = "\n".join(buf).strip()
            name, buf = header.group(1).strip(), []
            continue
        if name is None:  # pre-section directives / comments
            found = _DEFAULT_RE.match(line.strip())
            if found:
                default = found.group(1).strip()
            continue
        if line.startswith("#"):
            continue  # a comment inside a section
        buf.append(line)
    if name is not None:
        descs[name] = "\n".join(buf).strip()
    return {n: d for n, d in descs.items() if d}, default


def load_themes(faces_dir: str | Path) -> Themes:
    """Load the theme manifest + auto-discovered folders into a :class:`Themes`.

    The manifest describes themes and names the default; discovered folders without a manifest
    entry are included with an empty description. The default is the manifest's (if it's a known
    theme), else the first discovered theme, else ``None`` (→ the flat v0.7 behavior).
    """
    faces = Path(faces_dir)
    descs, default = _parse_manifest(faces / "themes.md")
    discovered = discover_themes(faces)
    names = set(discovered) | set(descs)
    descriptions = {n: descs.get(n, "") for n in names}
    if default not in descriptions:
        default = discovered[0] if discovered else None
    return Themes(descriptions=descriptions, default=default)

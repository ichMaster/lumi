"""The desktop window's portrait pick (v2.12.2, LUMI-271): which picture of the client's own face pack goes with a
state — ``(theme, emotion, intensity)`` → a path inside the pack. Pure; the proxy serves the answer locally
(``/face``, ``/faces/``), so no face ever crosses the network.

The folder comes from the v0.11 wardrobe resolver, ``viewer.face.resolve_variants``: ``<theme>/<emotion>/`` → the
theme's ``calm/`` → the default theme's → the flat v0.7 image (``<emotion>[_low|_high].png``, already chosen by
intensity there). Inside a folder whose pictures are ``01`` / ``02`` / ``03`` — the packs name them by intensity
(``place-faces``: mid, low, high) — **intensity chooses** (the owner's decision, 2026-11-14); any other folder falls
back to the viewer's random pick without an immediate repeat. The old Tkinter viewer keeps its own random pick.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from viewer.face import _band, _normalize, pick_variant, resolve_variants

BY_BAND = {"low": "02.png", "mid": "01.png", "high": "03.png"}  # the place-faces naming


@dataclass(frozen=True)
class Pick:
    """A portrait for a state: ``rel`` is its path inside the pack (``/``-separated), ``band`` the intensity band."""

    rel: str
    theme: str | None
    emotion: str
    band: str


class FacePicker:
    """Picks portraits from ``faces_dir``; remembers its last pick so a random fallback never repeats at once."""

    def __init__(self, faces_dir: Path, *, default_theme: str | None = None, rng: random.Random | None = None) -> None:
        self.faces_dir = Path(faces_dir)
        self.default_theme = default_theme
        self.rng = rng or random.Random()
        self._last: Path | None = None

    @classmethod
    def for_pack(cls, faces_dir: Path) -> FacePicker:
        """A picker with the pack's own default theme (``themes.md`` — ``None`` → the flat everyday pack)."""
        from viewer.themes import load_themes

        return cls(faces_dir, default_theme=load_themes(faces_dir).default)

    @property
    def available(self) -> bool:
        return self.faces_dir.is_dir()

    def pick(self, theme: str | None, emotion: str, intensity: float | None) -> Pick | None:
        """The portrait for the state, or ``None`` when the pack has no picture at all."""
        name = _normalize(emotion)
        band = _band(float(intensity)) if intensity is not None else "mid"
        theme = theme if theme and (self.faces_dir / theme).is_dir() else None
        variants = resolve_variants(name, intensity, self.faces_dir, theme=theme, default_theme=self.default_theme)
        existing = [v for v in variants if v.is_file()]
        if not existing:
            return None
        by_name = {v.name: v for v in existing}
        chosen = by_name.get(BY_BAND[band]) if set(BY_BAND.values()) <= set(by_name) else None
        if chosen is None:
            chosen = pick_variant(existing, previous=self._last, rng=self.rng)
        self._last = chosen
        try:
            rel = chosen.resolve().relative_to(self.faces_dir.resolve()).as_posix()
        except ValueError:
            return None
        used = rel.split("/")[0] if "/" in rel else None
        return Pick(rel=rel, theme=used, emotion=name, band=band)


def emoji_table(path: Path) -> dict[str, list[str]]:
    """The TUI's authored emotion → emoji map (``core/emoji.md``, ``LUMI_EMOJI_PATH``) as JSON-ready data:
    ``{emotion: [low, mid, high]}``, total over the enum (LUMI-276 — her emotion under each reply)."""
    from core.emoji import load_emoji_map

    return {emotion.value: list(row) for emotion, row in load_emoji_map(path).items()}


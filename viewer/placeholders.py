"""Placeholder face pack (v0.7) — simple per-emotion PNGs so the viewer runs before art.

Real portraits (prompts in `viewer/faces/PROMPTS.md`) dropped into `faces/` override these;
`calm.png` is the fallback. Each placeholder is a soft colored card with the emotion name.
Headless (Pillow only) — no display needed, so it's testable in CI.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from core.emotion import Emotion

CANVAS = (768, 768)

# A soft background color per emotion (just to tell the placeholders apart).
_COLORS: dict[Emotion, tuple[int, int, int]] = {
    Emotion.JOY: (255, 214, 102),
    Emotion.CALM: (158, 196, 196),
    Emotion.PLAYFUL: (244, 162, 196),
    Emotion.TENDER: (245, 182, 200),
    Emotion.THOUGHTFUL: (170, 178, 214),
    Emotion.SERIOUS: (150, 150, 162),
    Emotion.SURPRISE: (255, 196, 120),
    Emotion.DOUBT: (202, 188, 150),
    Emotion.SAD: (140, 160, 196),
}


def generate_placeholders(
    faces_dir: str | Path, *, size: tuple[int, int] = CANVAS, overwrite: bool = False
) -> list[Path]:
    """Write a placeholder ``<emotion>.png`` for each of the 9 emotions; return the paths.

    Existing files are kept (so real art isn't clobbered) unless ``overwrite=True``.
    """
    faces = Path(faces_dir)
    faces.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for emotion in Emotion:
        path = faces / f"{emotion.value}.png"
        if path.exists() and not overwrite:
            continue
        img = Image.new("RGB", size, _COLORS[emotion])
        draw = ImageDraw.Draw(img)
        draw.text((size[0] // 2, size[1] // 2), emotion.value, fill=(28, 28, 38), anchor="mm")
        img.save(path)
        written.append(path)
    return written

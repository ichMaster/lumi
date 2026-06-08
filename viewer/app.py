"""The local face window (v0.7) — a small pygame window showing Лілі's current face.

Polls the emotion signal (via :class:`~viewer.face.FaceSwitcher`) and shows
`faces/<emotion>.png`, redrawing only when it changes; `calm` is the fallback so the
window never breaks. The poll/resolve/fallback logic is in `viewer/face.py` (tested
without a display); this is the thin GUI shell. ``pygame`` is imported lazily inside
:func:`run` (it needs a display — kept out of the import/test path).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from core.emotion import DEFAULT_EMOTION
from viewer.face import FaceSwitcher

POLL_MS = 700  # re-read the signal ~every 0.7 s


def png_to_surface(path: str | Path, size: tuple[int, int]):
    """Load a PNG via **Pillow** → a pygame ``Surface`` scaled to ``size``.

    pygame's bundled image loader only handles BMP in this build (no SDL_image), so
    PNGs are decoded with Pillow and handed to pygame as raw RGB bytes.
    """
    import pygame

    with Image.open(path) as im:
        rgb = im.convert("RGB")
        surface = pygame.image.frombytes(rgb.tobytes(), rgb.size, "RGB")
    return pygame.transform.smoothscale(surface, size)


def run(
    signal_path: str | Path,
    faces_dir: str | Path,
    *,
    poll_ms: int = POLL_MS,
    size=(512, 512),
    idle_timeout: float | None = None,
) -> None:  # pragma: no cover - requires a display
    """Open the window and loop: poll the signal → show the matching face. Blocks until closed.

    With ``idle_timeout`` the face relaxes to `calm` after that many seconds of no change.
    """
    import pygame

    faces = Path(faces_dir)
    pygame.init()
    screen = pygame.display.set_mode(size)
    pygame.display.set_caption("Лілі")
    switcher = FaceSwitcher(signal_path, faces, idle_timeout=idle_timeout)
    clock = pygame.time.Clock()
    surface = None

    def show(path: Path) -> None:
        nonlocal surface
        try:
            surface = png_to_surface(path, size)
        except (pygame.error, FileNotFoundError, OSError, ValueError):
            surface = None

    # Draw the first frame immediately (calm if the signal isn't resolvable yet).
    show(switcher.poll() or faces / f"{DEFAULT_EMOTION.value}.png")

    last_poll = 0
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
        now = pygame.time.get_ticks()
        if now - last_poll >= poll_ms:
            last_poll = now
            changed = switcher.poll()
            if changed is not None:
                show(changed)
        screen.fill((20, 20, 28))
        if surface is not None:
            screen.blit(surface, (0, 0))
        pygame.display.flip()
        clock.tick(30)
    pygame.quit()

"""Emotion → face-image resolver + signal reader (v0.7 + v0.11) — pure, testable, no GUI.

Resolves the current-emotion signal to a face image, **total over the emotion enum** with a
**`calm` fallback** (EMOTION.md §7/§8). v0.7 used flat `faces/<emotion>.png` (+ intensity
variants). v0.11 adds:

- **Variants** — each emotion is a *folder* (`faces/<theme>/<emotion>/*.png`); the viewer shows a
  **random** one (no immediate repeat) so she isn't predictable.
- **Themes** — each theme is a wardrobe pack (`faces/<theme>/…`); the signal carries the theme
  (``<theme> <emotion> <intensity>``). A bare ``<emotion> <intensity>`` → the default theme.

Fully backward-compatible: with no theme folders a flat `faces/<emotion>.png` behaves like v0.7.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from pathlib import Path

from core.emotion import DEFAULT_EMOTION, Emotion

Exists = Callable[[Path], bool]
Lister = Callable[[Path], list[Path]]  # list the *.png variants in a folder (empty if none)


def _band(intensity: float) -> str:
    """Intensity → band (mirrors the v0.5 emoji bands): low <0.34, mid, high ≥0.67."""
    if intensity < 0.34:
        return "low"
    if intensity < 0.67:
        return "mid"
    return "high"


def _normalize(emotion: str) -> str:
    """Coerce to a known enum value, else the neutral `calm` (EMOTION.md §8)."""
    try:
        return Emotion(str(emotion).strip().lower()).value
    except ValueError:
        return DEFAULT_EMOTION.value


def _is_emotion(token: str) -> bool:
    """Whether ``token`` is one of the 9 enum emotion names (used to spot a theme prefix)."""
    try:
        Emotion(str(token).strip().lower())
        return True
    except ValueError:
        return False


def face_for(
    emotion: str,
    intensity: float | None = None,
    faces_dir: str | Path = "faces",
    *,
    exists: Exists | None = None,
) -> Path:
    """Resolve ``emotion`` (+ optional ``intensity``) to a **flat** ``faces/<…>.png`` path (v0.7).

    Tries, in order: the intensity variant (if any), the base ``<emotion>.png``, then
    ``calm.png``. Returns the first that exists; if none do, returns ``calm.png`` anyway
    (always a path — total over the enum, never raises).
    """
    faces = Path(faces_dir)
    here = exists or (lambda p: p.is_file())
    name = _normalize(emotion)
    calm = faces / f"{DEFAULT_EMOTION.value}.png"

    candidates: list[Path] = []
    if intensity is not None:
        band = _band(float(intensity))
        if band != "mid":
            candidates.append(faces / f"{name}_{band}.png")
    candidates.append(faces / f"{name}.png")
    candidates.append(calm)

    for candidate in candidates:
        if here(candidate):
            return candidate
    return calm


def _default_lister(folder: Path) -> list[Path]:
    """The ``*.png`` files in ``folder`` (sorted), or ``[]`` when it isn't a directory."""
    return sorted(folder.glob("*.png")) if folder.is_dir() else []


def resolve_variants(
    emotion: str,
    intensity: float | None = None,
    faces_dir: str | Path = "faces",
    *,
    theme: str | None = None,
    default_theme: str | None = None,
    exists: Exists | None = None,
    lister: Lister | None = None,
) -> list[Path]:
    """Resolve ``(theme, emotion)`` to the **set of variant images** for the best-matching folder.

    Fallback chain (v0.11): ``faces/<theme>/<emotion>/`` → ``faces/<theme>/calm/`` →
    ``faces/<default_theme>/<emotion>/`` → ``faces/<default_theme>/calm/`` → the **flat** v0.7
    image (`faces/<emotion>.png`, as a one-element list). Always returns ≥1 path; never raises.
    """
    faces = Path(faces_dir)
    ls = lister or _default_lister
    name = _normalize(emotion)
    for theme_name in (theme, default_theme):
        if not theme_name:
            continue
        for emo in (name, DEFAULT_EMOTION.value):  # this emotion → the theme's calm fallback
            pngs = ls(faces / theme_name / emo)
            if pngs:
                return pngs
    # No theme variants → the flat v0.7 single image.
    return [face_for(name, intensity, faces, exists=exists)]


def pick_variant(
    variants: list[Path],
    *,
    previous: Path | None = None,
    rng: random.Random | None = None,
) -> Path | None:
    """Pick one variant at **random, avoiding an immediate repeat** of ``previous``.

    ``None`` only when ``variants`` is empty. With a single variant it's returned as-is.
    """
    if not variants:
        return None
    if len(variants) == 1:
        return variants[0]
    pool = [v for v in variants if v != previous] or variants
    return (rng or random).choice(pool)


def parse_signal(raw: str) -> tuple[str | None, str, float | None]:
    """Parse a signal line → ``(theme, emotion, intensity)`` (v0.11).

    Formats: ``<theme> <emotion> <intensity> [date time]`` or the bare ``<emotion> <intensity>
    [date time]`` (→ ``theme=None``, the default theme). The theme is the leading token **only
    when it isn't itself an emotion name**; trailing tokens (the per-turn date-time) are ignored.
    """
    parts = raw.split()
    if not parts:
        return None, DEFAULT_EMOTION.value, None
    theme: str | None = None
    rest = parts
    # A theme prefix only when the lead token isn't an emotion AND the next one is.
    if len(parts) >= 2 and not _is_emotion(parts[0]) and _is_emotion(parts[1]):
        theme, rest = parts[0], parts[1:]
    emotion = _normalize(rest[0])
    intensity: float | None = None
    if len(rest) > 1:
        try:
            intensity = float(rest[1])
        except ValueError:
            intensity = None
    return theme, emotion, intensity


def read_signal(path: str | Path) -> tuple[str | None, str, float | None]:
    """Read the signal file → ``(theme, emotion, intensity)``. Missing/garbled → ``(None, 'calm', None)``."""
    try:
        return parse_signal(Path(path).read_text(encoding="utf-8").strip())
    except OSError:
        return None, DEFAULT_EMOTION.value, None


class FaceSwitcher:
    """Polls the signal → resolves a (random) face, reporting only when the image **changed**.

    The viewer ticks :meth:`poll`; a non-``None`` return is a new path to draw, ``None`` means
    "no change". On each **new turn** (the signal line changes, incl. its timestamp) it re-picks a
    **random** variant for ``(theme, emotion)`` with **no immediate repeat**, so the same emotion
    shows a different picture turn to turn.

    **Idle relax (EMOTION.md ``ttl_ms``).** With ``idle_timeout`` set, an unchanged signal relaxes
    the face to the theme's ``calm`` once; the next signal change wakes it. ``clock`` is injectable.
    """

    def __init__(
        self,
        signal_path: str | Path,
        faces_dir: str | Path,
        *,
        exists: Exists | None = None,
        lister: Lister | None = None,
        default_theme: str | None = None,
        idle_timeout: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self._signal = Path(signal_path)
        self._faces = Path(faces_dir)
        self._exists = exists
        self._lister = lister
        self._default_theme = default_theme
        self._idle_timeout = idle_timeout
        self._clock = clock
        self._rng = rng or random.Random()
        self._shown: Path | None = None
        self._last_raw: str | None = None  # the full signal line (incl. timestamp) — change key
        self._last_theme: str | None = default_theme
        self._last_change: float = 0.0
        self._relaxed: bool = False

    @property
    def current(self) -> Path | None:
        return self._shown

    def _pick(self, theme: str | None, emotion: str, intensity: float | None) -> Path | None:
        variants = resolve_variants(
            emotion, intensity, self._faces,
            theme=theme, default_theme=self._default_theme,
            exists=self._exists, lister=self._lister,
        )
        return pick_variant(variants, previous=self._shown, rng=self._rng)

    def poll(self, now: float | None = None) -> Path | None:
        """Re-read the signal; return the new face to draw, or ``None`` for no change."""
        now = self._clock() if now is None else now
        try:
            raw = self._signal.read_text(encoding="utf-8").strip()
        except OSError:
            raw = ""
        theme, emotion, intensity = parse_signal(raw)
        if raw != self._last_raw:  # a new turn → re-pick a (random, non-repeating) variant
            self._last_raw = raw
            self._last_change = now
            self._last_theme = theme
            self._relaxed = False
            path = self._pick(theme, emotion, intensity)
            if path is not None and path != self._shown:
                self._shown = path
                return path
            return None
        # signal unchanged → relax to the theme's calm once, after the idle timeout
        if (
            self._idle_timeout is not None
            and not self._relaxed
            and (now - self._last_change) >= self._idle_timeout
        ):
            self._relaxed = True
            calm = self._pick(self._last_theme, DEFAULT_EMOTION.value, None)
            if calm is not None and calm != self._shown:
                self._shown = calm
                return calm
        return None

"""Emotion → face-image resolver + signal reader (v0.7) — pure, testable, no GUI.

Resolves the current-emotion signal (LUMI-028) to a `faces/<…>.png` path, **total over
the emotion enum** with a **`calm` fallback** (EMOTION.md §7/§8). Intensity variants
(`<emotion>_low.png` / `<emotion>_high.png`, by the v0.5 bands) are used only when their
files exist. The viewer (LUMI-030) is a thin shell over this.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from core.emotion import DEFAULT_EMOTION, Emotion

Exists = Callable[[Path], bool]


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


def face_for(
    emotion: str,
    intensity: float | None = None,
    faces_dir: str | Path = "faces",
    *,
    exists: Exists | None = None,
) -> Path:
    """Resolve ``emotion`` (+ optional ``intensity``) to a ``faces/<…>.png`` path.

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


def read_signal(path: str | Path) -> tuple[str, float | None]:
    """Read the one-word (+ optional intensity) signal. Missing/garbled → ``('calm', None)``."""
    try:
        raw = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_EMOTION.value, None
    if not raw:
        return DEFAULT_EMOTION.value, None
    parts = raw.split()
    emotion = _normalize(parts[0])
    intensity: float | None = None
    if len(parts) > 1:
        try:
            intensity = float(parts[1])
        except ValueError:
            intensity = None
    return emotion, intensity


class FaceSwitcher:
    """Polls the signal → resolves the face, reporting only when the image **changed**.

    The viewer ticks :meth:`poll`; a non-``None`` return is a new path to draw, ``None``
    means "no change, keep the current image".
    """

    def __init__(
        self,
        signal_path: str | Path,
        faces_dir: str | Path,
        *,
        exists: Exists | None = None,
    ) -> None:
        self._signal = Path(signal_path)
        self._faces = Path(faces_dir)
        self._exists = exists
        self._current: Path | None = None

    @property
    def current(self) -> Path | None:
        return self._current

    def poll(self) -> Path | None:
        """Re-read the signal; return the new face path if it changed, else ``None``."""
        emotion, intensity = read_signal(self._signal)
        path = face_for(emotion, intensity, self._faces, exists=self._exists)
        if path != self._current:
            self._current = path
            return path
        return None

"""Emotion → face-image resolver + signal reader (v0.7) — pure, testable, no GUI.

Resolves the current-emotion signal (LUMI-028) to a `faces/<…>.png` path, **total over
the emotion enum** with a **`calm` fallback** (EMOTION.md §7/§8). Intensity variants
(`<emotion>_low.png` / `<emotion>_high.png`, by the v0.5 bands) are used only when their
files exist. The viewer (LUMI-030) is a thin shell over this.
"""

from __future__ import annotations

import time
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


def parse_signal(raw: str) -> tuple[str, float | None]:
    """Parse a signal line ``<emotion> [intensity] [date time]`` → ``(emotion, intensity)``.

    Tokens after the intensity (the per-turn date-time the core writes) are ignored for the
    face; the raw line is what :class:`FaceSwitcher` uses to detect a new turn.
    """
    parts = raw.split()
    if not parts:
        return DEFAULT_EMOTION.value, None
    emotion = _normalize(parts[0])
    intensity: float | None = None
    if len(parts) > 1:
        try:
            intensity = float(parts[1])
        except ValueError:
            intensity = None
    return emotion, intensity


def read_signal(path: str | Path) -> tuple[str, float | None]:
    """Read the signal file → ``(emotion, intensity)``. Missing/garbled → ``('calm', None)``."""
    try:
        return parse_signal(Path(path).read_text(encoding="utf-8").strip())
    except OSError:
        return DEFAULT_EMOTION.value, None


class FaceSwitcher:
    """Polls the signal → resolves the face, reporting only when the image **changed**.

    The viewer ticks :meth:`poll`; a non-``None`` return is a new path to draw, ``None``
    means "no change, keep the current image".

    **Idle relax (EMOTION.md ``ttl_ms``).** With ``idle_timeout`` set, if the signal hasn't
    changed for that many seconds the face relaxes to the **default** (`calm`); the next
    signal change wakes it again. ``clock`` is injectable (real seconds by default); tests
    pass an explicit ``now`` to :meth:`poll`.
    """

    def __init__(
        self,
        signal_path: str | Path,
        faces_dir: str | Path,
        *,
        exists: Exists | None = None,
        idle_timeout: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._signal = Path(signal_path)
        self._faces = Path(faces_dir)
        self._exists = exists
        self._idle_timeout = idle_timeout
        self._clock = clock
        self._default = face_for(DEFAULT_EMOTION.value, None, self._faces, exists=exists)
        self._shown: Path | None = None
        self._last_raw: str | None = None  # the full signal line (incl. timestamp) — change key
        self._last_change: float = 0.0

    @property
    def current(self) -> Path | None:
        return self._shown

    def poll(self, now: float | None = None) -> Path | None:
        """Re-read the signal; return the new face to draw, or ``None`` for no change.

        A changed signal shows the new face and resets the idle timer; an unchanged signal
        past ``idle_timeout`` relaxes to the default (`calm`).
        """
        now = self._clock() if now is None else now
        try:
            raw = self._signal.read_text(encoding="utf-8").strip()
        except OSError:
            raw = ""
        emotion, intensity = parse_signal(raw)
        path = face_for(emotion, intensity, self._faces, exists=self._exists)
        if raw != self._last_raw:  # the signal line changed (incl. its timestamp) → a new turn
            self._last_raw = raw
            self._last_change = now
            if path != self._shown:
                self._shown = path
                return path
            return None
        # signal unchanged → relax to the default after the idle timeout
        if (
            self._idle_timeout is not None
            and self._shown != self._default
            and (now - self._last_change) >= self._idle_timeout
        ):
            self._shown = self._default
            return self._default
        return None

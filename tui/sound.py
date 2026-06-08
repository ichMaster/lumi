"""Optional UI sounds for the TUI (v0.7.x) — a short blip on send + receive.

Backends, tried in order (lazily, on first use):
  1. **macOS ``afplay``** with built-in system sounds (`/System/Library/Sounds/*.aiff`) —
     the reliable path on darwin; no bundled files.
  2. **pygame.mixer** with two runtime-synthesized tones — for other platforms / full
     pygame builds. (This machine's pygame is a minimal build with no mixer module.)
  3. none → a silent no-op.

Everything is best-effort: with no backend, nothing plays and nothing raises. *Whether* a
sound plays is the app's call (it gates on the F2 toggle and never sounds the idle nudge);
this module only knows *how*.
"""

from __future__ import annotations

import array
import math
import os
import shutil
import subprocess
import sys

# macOS system sounds (present on every mac) — distinct send / receive.
_MAC_SOUNDS = "/System/Library/Sounds"
_MAC_SEND = f"{_MAC_SOUNDS}/Tink.aiff"  # light tick on send
_MAC_RECEIVE = f"{_MAC_SOUNDS}/Glass.aiff"  # soft chime on receive


def synth_tone(freq: float, ms: int, *, rate: int = 44100, volume: float = 0.55) -> bytes:
    """A short mono int16 sine tone with an 8 ms fade in/out (so there's no click)."""
    n = int(rate * ms / 1000)
    fade = max(1, int(rate * 0.008))
    amp = volume * 32767
    out = array.array("h")
    for i in range(n):
        if i < fade:
            env = i / fade
        elif i > n - fade:
            env = (n - i) / fade
        else:
            env = 1.0
        out.append(int(amp * env * math.sin(2.0 * math.pi * freq * i / rate)))
    return out.tobytes()


class SoundPlayer:
    """Plays a send / receive blip via the first available backend (chosen lazily).

    Call :meth:`ensure` to pick a backend and learn if any is available (used by the
    F2 toggle); :meth:`send` / :meth:`receive` play the blips (no-op if none).
    """

    def __init__(self) -> None:
        self._backend: str | None = None  # "afplay" | "pygame" | None
        self._tried = False
        self._afplay: str | None = None
        self._send_snd: object | None = None
        self._receive_snd: object | None = None

    def ensure(self) -> bool:
        """Pick a backend on first call; return True if any audio backend is available."""
        if self._tried:
            return self._backend is not None
        self._tried = True
        # 1) macOS afplay + built-in system sounds.
        if sys.platform == "darwin":
            afplay = shutil.which("afplay")
            if afplay and os.path.exists(_MAC_SEND) and os.path.exists(_MAC_RECEIVE):
                self._afplay = afplay
                self._backend = "afplay"
                return True
        # 2) pygame.mixer with synthesized tones (absent in minimal pygame builds).
        try:
            os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
            import pygame.mixer

            pygame.mixer.init(frequency=44100, size=-16, channels=1)
            self._send_snd = pygame.mixer.Sound(buffer=synth_tone(880, 70))
            self._receive_snd = pygame.mixer.Sound(buffer=synth_tone(523, 120))
            self._send_snd.set_volume(0.4)
            self._receive_snd.set_volume(0.4)
            self._backend = "pygame"
            return True
        except Exception:  # noqa: BLE001 — no mixer module / no audio device → next backend
            pass
        return False

    def send(self) -> None:
        self._play("send")

    def receive(self) -> None:
        self._play("receive")

    def _play(self, kind: str) -> None:
        if not self.ensure():
            return
        try:
            if self._backend == "afplay":
                path = _MAC_SEND if kind == "send" else _MAC_RECEIVE
                subprocess.Popen(
                    [self._afplay, path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            elif self._backend == "pygame":
                snd = self._send_snd if kind == "send" else self._receive_snd
                if snd is not None:
                    snd.play()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — never let a sound error reach the UI
            pass
